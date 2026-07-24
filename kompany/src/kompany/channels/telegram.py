"""Telegram channel adapter — long-poll worker over the CEO channel.

PRD ``06-12-channels`` D2: getUpdates long-poll (stdlib urllib, no new
deps), allow-list via the existing ``telegram_allowed_chat_ids``
setting, replies via sendMessage. Runs as an asyncio worker started by
``engine.start()`` when ``telegram_bot_token`` is set (watchdog/ticker
precedent: injectable transport + sleeper for tests).

The adapter NEVER reasons (D1): every authorized message goes straight
to ``engine.process_directive`` with a transport-neutral context. Session
mapping is isolated by account, chat, thread, sender, project, and active agent
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

from kompany.channels.context import DirectiveContext
from kompany.core.event_hub import get_event_hub
from kompany.core.credential_broker import CredentialActionRequired
from kompany.core.drain import get_drain_registry
from kompany.remote import request_from_telegram_update
from kompany.state.channel_sessions_map import ChannelSessionMapStore
from kompany.state.channel_progress import ChannelProgressStore
from kompany.state.models import SESSION_TERMINAL_STATUSES, SessionStatus

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
        self._event_task: asyncio.Task[None] | None = None
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
        self._event_task = loop.create_task(self._event_loop())

    async def stop(self) -> None:
        tasks = [
            task for task in (self._task, self._event_task)
            if task is not None and not task.done()
        ]
        self._task = None
        self._event_task = None
        self._stopped.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
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
        """Authorize, replay-guard, route to the engine, reply.

        Drain tracking: wraps the whole handler (not just the
        process_directive call) so the deployment plan's ready_for_restart
        check waits for any in-flight Telegram message — including the
        authorization/replay-guard bookkeeping and the reply send, not just
        engine dispatch — before reporting the runtime drained. Rejection
        of *new* directive work while suspended is already handled inside
        ``process_directive`` (directive_flow.py); this tracker only counts
        in-flight handling, it does not gate.
        """
        with get_drain_registry().track("channel_handler"):
            return self._handle_update_inner(update)

    def _handle_update_inner(self, update: dict[str, Any]) -> dict[str, Any]:
        callback = update.get("callback_query")
        if callback:
            return self._handle_callback(callback)
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
        context = DirectiveContext(
            channel=req.source,
            account_id=req.account_id,
            chat_id=req.chat_id,
            thread_id=req.thread_id,
            sender_id=req.sender_id,
        )
        outcome = self._route(req.chat_id, req.text, context)
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

    @property
    def _progress(self) -> ChannelProgressStore:
        return ChannelProgressStore(self._engine.db)

    def _authorized(self, chat_id: str) -> bool:
        raw = getattr(self._engine.settings, "telegram_allowed_chat_ids", "")
        allowed = {c.strip() for c in str(raw).split(",") if c.strip()}
        return bool(allowed) and str(chat_id) in allowed

    def _session_for(
        self,
        session_key: str,
        text: str,
        *,
        legacy_key: str | None = None,
    ) -> str | None:
        """Mapped session id, dropping mappings to closed or gated sessions.

        Terminal sessions (dispatched/answered/abandoned) are obviously done.
        Gated/proposed sessions are non-terminal but are waiting for an
        explicit GO/give-up on a *specific* pending action — a new freeform
        Telegram message is not that GO, and reusing the session makes the
        directive flow try to transition gated→clarifying, which the
        conversation state machine rejects (IllegalSessionTransition). So
        we drop the mapping and let ``process_directive`` open a fresh
        session for the new message. The gated session stays in the store
        for the board SPA to resolve via its own UI.
        """
        session_id = self._sessions.get(session_key)
        if (
            not session_id
            and legacy_key
            and legacy_key != session_key
        ):
            legacy_session_id = self._sessions.get(legacy_key)
            legacy_session = (
                self._engine.channel.get_session(legacy_session_id)
                if legacy_session_id
                else None
            )
            if legacy_session is not None and all(
                value is None
                for value in (
                    legacy_session.channel,
                    legacy_session.account_id,
                    legacy_session.chat_id,
                    legacy_session.thread_id,
                    legacy_session.sender_id,
                )
            ):
                session_id = legacy_session_id
                self._sessions.set(session_key, session_id)
                self._sessions.clear(legacy_key)
        if not session_id:
            return None
        session = self._engine.channel.get_session(session_id)
        if session is None or session.state in SESSION_TERMINAL_STATUSES:
            self._sessions.clear(session_key)
            return None
        if session.state in (SessionStatus.GATED, SessionStatus.PROPOSED):
            reply = text.strip().lower()
            if reply in {"go", "g", "yes", "y", "abandon", "a", "no", "n"}:
                return session_id
            self._sessions.clear(session_key)
            return None
        return session_id

    def _route(
        self,
        chat_id: str,
        text: str,
        context: DirectiveContext,
    ) -> dict[str, Any]:
        """Adapter contract (D1): translate, never reason."""
        session_key = context.session_key
        session_id = self._session_for(
            session_key,
            text,
            legacy_key=chat_id,
        )
        result = self._engine.process_directive(
            text,
            session_id=session_id,
            context=context,
        )
        active_agent = (result.active_agent_id or "ceo").upper()
        project = result.project_id or "General"
        projects = getattr(self._engine, "projects", None)
        if result.project_id and projects is not None:
            resolved = projects.get(result.project_id)
            if resolved is not None:
                project = resolved.name
        header = f"{active_agent} · {project}"
        if (
            result.previous_agent_id
            and result.previous_agent_id != result.active_agent_id
        ):
            transition = (
                f"{result.previous_agent_id.upper()} → {active_agent}"
            )
            header = f"{transition}\n{header}"
        body = result.message or f"({result.status})"
        participants = [
            agent.upper()
            for agent in result.agents_used
            if agent.lower() != "ceo"
        ]
        if result.delegation_id:
            body = (
                f"{body}\n\n"
                f"Agents: {', '.join(participants) or 'Pending'}\n"
                f"Phase: {(result.delegation_status or 'queued').title()}\n"
                f"Elapsed: 0m\n"
                f"Cost: ${result.total_ai_cost:.2f}\n"
                "Pending action: None"
            )
        reply = f"{header}\n\n{body}"
        if result.status in {"gated", "proposed"}:
            reply = f"{reply}{GATED_HINT}"
        persisted = (
            self._engine.channel.get_session(result.session_id)
            if result.session_id
            else None
        )
        persisted_session_is_live = bool(
            persisted is not None
            and persisted.state not in SESSION_TERMINAL_STATUSES
        )
        if result.session_id and (
            result.conversation_continues
            or result.status in _CONTINUE_STATUSES
            or (
                result.status == "failed"
                and persisted_session_is_live
            )
        ):
            self._sessions.set(session_key, result.session_id)
        else:
            self._sessions.clear(session_key)
        reply_markup = None
        if result.delegation_id:
            reply_markup = {
                "inline_keyboard": [
                    [{
                        "text": "Cancel",
                        "callback_data": (
                            f"delegation:cancel:{result.delegation_id}"
                        ),
                    }],
                    [
                        {
                            "text": "Refresh",
                            "callback_data": (
                                f"delegation:refresh:"
                                f"{result.delegation_id}"
                            ),
                        },
                        {
                            "text": "Details",
                            "callback_data": (
                                f"delegation:details:"
                                f"{result.delegation_id}"
                            ),
                        },
                    ],
                ],
            }
        sent = self._send(
            chat_id,
            reply,
            thread_id=context.thread_id,
            reply_markup=reply_markup,
        )
        if result.delegation_id and sent is not None:
            telegram_result = sent.get("result") or {}
            message_id = telegram_result.get("message_id")
            if message_id is not None:
                self._progress.set(
                    result.delegation_id,
                    channel="telegram",
                    chat_id=chat_id,
                    sender_id=context.sender_id,
                    thread_id=context.thread_id,
                    message_id=str(message_id),
                    project_name=project,
                    agents=participants,
                    cost_usd=result.total_ai_cost,
                )
        return {
            "status": result.status,
            "session_id": result.session_id,
            "reply": reply,
        }

    def _send(
        self,
        chat_id: str,
        text: str,
        *,
        thread_id: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            params = {"chat_id": chat_id, "text": text[:4000]}
            if thread_id:
                params["message_thread_id"] = thread_id
            if reply_markup:
                params["reply_markup"] = reply_markup
            return self._transport(
                "sendMessage",
                params,
            )
        except Exception:  # noqa: BLE001 — reply delivery is best-effort
            log.exception("telegram sendMessage failed")
            return None

    def _handle_callback(
        self,
        callback: dict[str, Any],
    ) -> dict[str, Any]:
        callback_id = str(callback.get("id") or "")
        message = callback.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id") or "")
        data = str(callback.get("data") or "")
        if not chat_id or not self._authorized(chat_id):
            return {"status": "ignored", "reason": "unauthorized"}
        credential_parts = data.split(":")
        if (
            len(credential_parts) == 5
            and credential_parts[:2] == ["credential", "retry"]
        ):
            _, _, delegation_id, task_id, action_id = credential_parts
            target = self._progress.get(delegation_id)
            callback_message_id = str(message.get("message_id") or "")
            callback_sender_id = str(
                (callback.get("from") or {}).get("id") or ""
            )
            if (
                target is None
                or str(target["chat_id"]) != chat_id
                or str(target["message_id"]) != callback_message_id
                or (
                    str(target.get("sender_id") or "")
                    and str(target["sender_id"]) != callback_sender_id
                )
            ):
                return {
                    "status": "ignored",
                    "reason": "delegation_context_mismatch",
                }
            try:
                delegation = self._engine.resume_credential_task(
                    task_id,
                    action_id,
                )
            except CredentialActionRequired as exc:
                self.handle_credential_action_event({
                    "delegation_id": delegation_id,
                    "task_id": task_id,
                    "action": exc.action.model_dump(mode="json"),
                })
                try:
                    self._transport(
                        "answerCallbackQuery",
                        {
                            "callback_query_id": callback_id,
                            "text": "Another credential action is required.",
                        },
                    )
                except Exception:  # noqa: BLE001 — blocker is durable
                    log.exception("telegram answerCallbackQuery failed")
                return {
                    "status": "blocked",
                    "delegation_id": delegation_id,
                    "task_id": task_id,
                }
            resumed_status = delegation.status.value
            if resumed_status == "pending":
                resumed_status = "active"
            try:
                self._transport(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                        "text": "Credential check passed; task resumed.",
                    },
                )
            except Exception:  # noqa: BLE001 — resume already persisted
                log.exception("telegram answerCallbackQuery failed")
            self.handle_delegation_event({
                "delegation_id": delegation_id,
                "status": resumed_status,
            })
            return {
                "status": resumed_status,
                "delegation_id": delegation_id,
                "task_id": task_id,
            }
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[0] != "delegation":
            return {"status": "ignored", "reason": "unsupported_callback"}
        action, delegation_id = parts[1], parts[2]
        if action not in {"cancel", "refresh", "details"}:
            return {"status": "ignored", "reason": "unsupported_callback"}
        target = self._progress.get(delegation_id)
        callback_message_id = str(message.get("message_id") or "")
        if (
            target is None
            or str(target["chat_id"]) != chat_id
            or str(target["message_id"]) != callback_message_id
        ):
            return {
                "status": "ignored",
                "reason": "delegation_context_mismatch",
            }
        if action != "cancel":
            delegation = self._engine.get_delegation(delegation_id)
            if delegation is None:
                return {"status": "ignored", "reason": "delegation_not_found"}
            terminal = {
                "completed",
                "delivered",
                "blocked",
                "failed",
                "cancelled",
            }
            completed_tasks = sum(
                child.status.value in terminal
                for child in delegation.children
            )
            child_cost = sum(
                float((child.result or {}).get("cost") or 0.0)
                for child in delegation.children
            )
            if action == "refresh":
                self.handle_delegation_event({
                    "delegation_id": delegation_id,
                    "status": delegation.status.value,
                    "completed_tasks": completed_tasks,
                    "total_tasks": len(delegation.children),
                    "cost_usd": child_cost,
                })
            else:
                statuses = ", ".join(
                    f"{child.assigned_agent.upper()}: "
                    f"{child.status.value.replace('_', ' ')}"
                    for child in delegation.children
                )
                self._send(
                    chat_id,
                    (
                        f"CEO · {target['project_name']}\n\n"
                        f"Delegation: {delegation.status.value}\n"
                        f"Tasks: {statuses or 'None'}\n"
                        f"Cost: ${child_cost:.2f}"
                    ),
                    thread_id=target["thread_id"],
                )
            try:
                self._transport(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                        "text": (
                            "Status refreshed."
                            if action == "refresh"
                            else "Details sent."
                        ),
                    },
                )
            except Exception:  # noqa: BLE001 — projection already completed
                log.exception("telegram answerCallbackQuery failed")
            return {
                "status": delegation.status.value,
                "delegation_id": delegation_id,
            }
        delegation = self._engine.cancel_delegation(delegation_id)
        try:
            self._transport(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "Delegation cancelled.",
                },
            )
        except Exception:  # noqa: BLE001 — cancellation already persisted
            log.exception("telegram answerCallbackQuery failed")
        self.handle_delegation_event({
            "delegation_id": delegation_id,
            "status": delegation.status.value,
        })
        return {
            "status": delegation.status.value,
            "delegation_id": delegation_id,
        }

    def handle_delegation_event(self, payload: dict[str, Any]) -> None:
        """Project one durable delegation milestone into its Telegram thread."""
        delegation_id = str(payload.get("delegation_id") or "")
        if not delegation_id:
            return
        target = self._progress.get(delegation_id)
        if target is None:
            return
        get_blocker = getattr(
            self._engine,
            "get_credential_action_for_delegation",
            None,
        )
        blocker = get_blocker(delegation_id) if get_blocker else None
        if blocker:
            self.handle_credential_action_event(blocker)
            return
        status = str(payload.get("status") or "active")
        labels = {
            "queued": "Queued",
            "active": "In progress",
            "synthesizing": "Synthesizing",
            "completed": "Completed",
            "failed": "Failed",
            "cancelled": "Cancelled",
        }
        label = labels.get(status, status.replace("_", " ").title())
        completed_tasks = payload.get("completed_tasks")
        total_tasks = payload.get("total_tasks")
        task_progress = ""
        if completed_tasks is not None and total_tasks is not None:
            task_progress = f"\nTasks: {completed_tasks}/{total_tasks}"
        agents = str(target.get("agents") or "").replace(",", ", ")
        created_at = str(target.get("created_at") or "")
        elapsed_minutes = 0
        if created_at:
            created = datetime.fromisoformat(
                created_at.replace(" ", "T")
            ).replace(tzinfo=UTC)
            elapsed_minutes = max(
                0,
                int((datetime.now(UTC) - created).total_seconds() // 60),
            )
        cost_usd = float(payload.get("cost_usd", target["cost_usd"]) or 0)
        text = (
            f"CEO · {target['project_name']}\n\n"
            f"Background delegation: {label}{task_progress}\n"
            f"Agents: {agents or 'Pending'}\n"
            f"Phase: {label}\n"
            f"Elapsed: {elapsed_minutes}m\n"
            f"Cost: ${cost_usd:.2f}\n"
            "Pending action: None"
        )
        try:
            message_id = target["message_id"]
            if str(message_id).isdigit():
                message_id = int(message_id)
            self._transport(
                "editMessageText",
                {
                    "chat_id": target["chat_id"],
                    "message_id": message_id,
                    "text": text[:4000],
                },
            )
        except Exception:  # noqa: BLE001 — preserve durable mapping for retry
            log.exception("telegram editMessageText failed")
            return
        if status == "completed" and payload.get("message"):
            self._send(
                target["chat_id"],
                (
                    f"CEO · {target['project_name']}\n\n"
                    f"{payload['message']}"
                ),
                thread_id=target["thread_id"],
            )
        if status in {"completed", "failed", "cancelled"}:
            self._progress.clear(delegation_id)

    def handle_credential_action_event(self, payload: dict[str, Any]) -> None:
        """Render a resumable credential blocker on a delegation status."""
        delegation_id = str(payload.get("delegation_id") or "")
        task_id = str(payload.get("task_id") or "")
        action = payload.get("action") or {}
        action_id = str(action.get("id") or "")
        if not delegation_id or not task_id or not action_id:
            return
        target = self._progress.get(delegation_id)
        if target is None:
            return
        labels = {
            "unlock": "Unlock credential store",
            "mfa": "Complete MFA",
            "reauth": "Re-authenticate",
            "replace": "Replace credential",
            "approval": "Approve credential use",
        }
        pending = labels.get(
            str(action.get("kind") or ""),
            "Resolve credential issue",
        )
        buttons: list[list[dict[str, str]]] = []
        action_url = str(action.get("action_url") or "")
        if action_url.startswith("https://"):
            buttons.append([{"text": pending, "url": action_url}])
        buttons.append([{
            "text": "Retry",
            "callback_data": (
                f"credential:retry:{delegation_id}:{task_id}:{action_id}"
            ),
        }])
        text = (
            f"CEO · {target['project_name']}\n\n"
            "Background delegation: Blocked\n"
            f"Agents: {str(target.get('agents') or '').replace(',', ', ') or 'Pending'}\n"
            "Phase: Waiting for credentials\n"
            f"Pending action: {pending}\n"
            f"Reason: {str(action.get('reason') or 'Credential action required')}"
        )
        try:
            message_id = target["message_id"]
            if str(message_id).isdigit():
                message_id = int(message_id)
            self._transport(
                "editMessageText",
                {
                    "chat_id": target["chat_id"],
                    "message_id": message_id,
                    "text": text[:4000],
                    "reply_markup": {"inline_keyboard": buttons},
                },
            )
        except Exception:  # noqa: BLE001 — durable blocker remains retryable
            log.exception("telegram credential action projection failed")

    async def _loop(self) -> None:
        log.debug("telegram worker loop started")
        try:
            while not self._stopped.is_set():
                try:
                    handled = await asyncio.to_thread(self.poll_once)
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

    async def _event_loop(self) -> None:
        hub = get_event_hub()
        hub.register_loop()
        try:
            list_blockers = getattr(
                self._engine,
                "list_credential_action_events",
                None,
            )
            if list_blockers:
                blockers = await asyncio.to_thread(list_blockers)
                for blocker in blockers:
                    await asyncio.to_thread(
                        self.handle_credential_action_event,
                        blocker,
                    )
            async for event in hub.subscribe():
                if self._stopped.is_set():
                    break
                if event["type"] in {
                    "delegation.milestone",
                    "delegation.completed",
                }:
                    await asyncio.to_thread(
                        self.handle_delegation_event,
                        event["data"],
                    )
                elif event["type"] == "credential.action_required":
                    await asyncio.to_thread(
                        self.handle_credential_action_event,
                        event["data"],
                    )
        except asyncio.CancelledError:
            log.debug("telegram delegation event loop cancelled")
            raise


__all__ = ["GATED_HINT", "REPLAY_SOURCE", "TelegramWorker", "build_urllib_transport"]
