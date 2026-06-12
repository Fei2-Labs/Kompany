"""Telegram channel adapter — long-poll worker over the CEO channel.

PRD ``06-12-channels`` D2: getUpdates long-poll (stdlib urllib, no new
deps), allow-list via the existing ``telegram_allowed_chat_ids``
setting, replies via sendMessage. Runs as an asyncio worker started by
``engine.start()`` when ``telegram_bot_token`` is set (watchdog/ticker
precedent: injectable transport + sleeper for tests).

The adapter NEVER reasons (D1): every authorized message goes straight
to ``engine.process_directive(text, session_id)``. Session mapping is
one ConversationSession per chat_id thread
(:class:`kompany.state.channel_sessions_map.ChannelSessionMapStore`),
kept across clarify/gated round-trips and reset on terminal states.
Replay protection reuses ``remote_command_replays`` keyed by update_id.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from urllib import request as urlrequest

from kompany.remote import request_from_telegram_update
from kompany.state.channel_sessions_map import ChannelSessionMapStore
from kompany.state.models import SESSION_TERMINAL_STATUSES

log = logging.getLogger(__name__)

# Replay-cache source. Distinct from the remote-command source
# ("telegram") so a /command processed by handle_remote_command and a
# chat message with the same update_id never collide.
REPLAY_SOURCE = "telegram_chat"

# States where the chat thread keeps continuing the same session.
_CONTINUE_STATUSES = {"clarify", "gated", "proposed", "awaiting_approval"}

GATED_HINT = "\n\nReply GO to proceed, or ABANDON to drop it."


def build_urllib_transport(bot_token: str) -> Callable[[str, dict], dict]:
    """Real Telegram Bot API transport (stdlib urllib, JSON POST)."""

    def transport(method: str, params: dict[str, Any]) -> dict[str, Any]:
        req = urlrequest.Request(
            f"https://api.telegram.org/bot{bot_token}/{method}",
            data=json.dumps(params).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = float(params.get("timeout", 0)) + 15.0
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return transport


class TelegramWorker:
    """Engine-scoped Telegram long-poll loop (ticker precedent)."""

    def __init__(
        self,
        engine: Any,
        transport: Callable[[str, dict], dict] | None = None,
        poll_timeout_seconds: int = 25,
        idle_sleep_seconds: float = 1.0,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ):
        self._engine = engine
        token = getattr(engine.settings, "telegram_bot_token", "")
        self._transport = (
            transport
            if transport is not None
            else build_urllib_transport(token)
        )
        self.poll_timeout_seconds = poll_timeout_seconds
        self.idle_sleep_seconds = idle_sleep_seconds
        self._sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self.offset: int = 0
        self.last_update_at: str | None = None
        self.updates_handled: int = 0

    # ------------------------------------------------------------------
    # Lifecycle (idempotent, watchdog/ticker pattern)
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.debug("telegram worker start outside a running loop; deferring")
            return
        self._stopped.clear()
        self._task = loop.create_task(self._loop())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        self._stopped.set()
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------
    # One poll pass (public for tests — tick_once / scan_once contract)
    # ------------------------------------------------------------------

    def poll_once(self) -> list[dict[str, Any]]:
        """Fetch pending updates, handle each, advance the offset."""
        response = self._transport(
            "getUpdates",
            {"offset": self.offset, "timeout": self.poll_timeout_seconds},
        )
        updates = (response or {}).get("result") or []
        outcomes: list[dict[str, Any]] = []
        for update in updates:
            update_id = update.get("update_id")
            if update_id is not None:
                self.offset = max(self.offset, int(update_id) + 1)
            try:
                outcomes.append(self.handle_update(update))
            except Exception:  # noqa: BLE001 — one bad update must not kill the loop
                log.exception("telegram update handling failed")
        return outcomes

    def handle_update(self, update: dict[str, Any]) -> dict[str, Any]:
        """Authorize, replay-guard, route to the engine, reply."""
        req = request_from_telegram_update(update)
        if not req.text or not req.chat_id:
            return {"status": "ignored", "reason": "empty"}
        if not self._authorized(req.chat_id):
            # Silently drop — never reply to unauthorized chats.
            return {"status": "ignored", "reason": "unauthorized"}
        replay_key = str(req.payload.get("update_id") or "")
        if replay_key:
            seen = self._engine.remote_replay.get(REPLAY_SOURCE, replay_key)
            if seen is not None:
                return {"status": "ignored", "reason": "replayed"}
        outcome = self._route(req.chat_id, req.text)
        if replay_key:
            self._engine.remote_replay.store(
                REPLAY_SOURCE, replay_key, "chat", {"status": outcome["status"]}
            )
        self.last_update_at = datetime.now(UTC).isoformat()
        self.updates_handled += 1
        return outcome

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @property
    def _sessions(self) -> ChannelSessionMapStore:
        return ChannelSessionMapStore(self._engine.db)

    def _authorized(self, chat_id: str) -> bool:
        raw = getattr(self._engine.settings, "telegram_allowed_chat_ids", "")
        allowed = {c.strip() for c in str(raw).split(",") if c.strip()}
        return bool(allowed) and str(chat_id) in allowed

    def _session_for(self, chat_id: str) -> str | None:
        """Mapped session id, dropping mappings to closed sessions."""
        session_id = self._sessions.get(chat_id)
        if not session_id:
            return None
        session = self._engine.channel.get_session(session_id)
        if session is None or session.state in SESSION_TERMINAL_STATUSES:
            self._sessions.clear(chat_id)
            return None
        return session_id

    def _route(self, chat_id: str, text: str) -> dict[str, Any]:
        """Adapter contract (D1): translate, never reason."""
        session_id = self._session_for(chat_id)
        result = self._engine.process_directive(text, session_id=session_id)
        reply = result.message or f"({result.status})"
        if result.status in {"gated", "proposed"}:
            reply = f"{reply}{GATED_HINT}"
        if result.session_id and result.status in _CONTINUE_STATUSES:
            self._sessions.set(chat_id, result.session_id)
        else:
            self._sessions.clear(chat_id)
        self._send(chat_id, reply)
        return {
            "status": result.status,
            "session_id": result.session_id,
            "reply": reply,
        }

    def _send(self, chat_id: str, text: str) -> None:
        try:
            self._transport(
                "sendMessage",
                {"chat_id": chat_id, "text": text[:4000]},
            )
        except Exception:  # noqa: BLE001 — reply delivery is best-effort
            log.exception("telegram sendMessage failed")

    async def _loop(self) -> None:
        log.debug("telegram worker loop started")
        try:
            while not self._stopped.is_set():
                try:
                    handled = self.poll_once()
                except Exception:  # noqa: BLE001 — transport hiccups must not kill the loop
                    log.exception("telegram poll failed")
                    handled = []
                if self._stopped.is_set():
                    break
                if not handled:
                    await self._sleeper(self.idle_sleep_seconds)
        except asyncio.CancelledError:
            log.debug("telegram worker loop cancelled")
            raise


__all__ = ["GATED_HINT", "REPLAY_SOURCE", "TelegramWorker", "build_urllib_transport"]
