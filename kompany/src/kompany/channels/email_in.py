"""Email-in channel — IMAP pull → founder triage cards.

PRD ``06-12-channels`` D4 (folds issue #17): poll an IMAP folder
(stdlib imaplib, no new deps) and turn each NEW mail into a
``channel_message`` approval-style triage card — read-only MVP, NOT an
auto-directive; the founder decides what (if anything) to do. Approve /
reject have no engine-side effect beyond resolving the card.

Idempotency: seen UIDs are persisted in ``channel_email_seen`` keyed by
``host/user/folder/uid`` so repeat polls never duplicate cards. The
poller runs inside the ticker's housekeeping cadence (every
``email_poll_every_ticks`` ticks) instead of its own worker — simpler
and daemon-friendly. The IMAP password lives in the credential vault
under ``email_imap_password`` (never in settings/YAML).
"""

from __future__ import annotations

import email
import email.header
import imaplib
import logging
from typing import Any, Callable

from kompany.state.models import ApprovalRequest

log = logging.getLogger(__name__)

ACTION_CHANNEL_MESSAGE = "channel_message"
VAULT_PASSWORD_KEY = "email_imap_password"
SNIPPET_CHARS = 300


def _decode(value: str) -> str:
    try:
        parts = email.header.decode_header(value)
        return "".join(
            p.decode(enc or "utf-8", "replace") if isinstance(p, bytes) else p
            for p, enc in parts
        )
    except Exception:  # noqa: BLE001 — header decoding is best-effort
        return value


def imap_fetch(
    host: str, user: str, password: str, folder: str
) -> list[dict[str, Any]]:
    """Real IMAP path: fetch headers + snippet of every message in folder."""
    mails: list[dict[str, Any]] = []
    conn = imaplib.IMAP4_SSL(host)
    try:
        conn.login(user, password)
        conn.select(folder, readonly=True)
        _typ, data = conn.uid("search", None, "ALL")
        uids = (data[0] or b"").split()
        for uid in uids[-50:]:  # bound one poll pass
            _typ, msg_data = conn.uid("fetch", uid, "(RFC822.HEADER)")
            raw = msg_data[0][1] if msg_data and msg_data[0] else b""
            msg = email.message_from_bytes(raw)
            mails.append(
                {
                    "uid": uid.decode("ascii", "replace"),
                    "from": _decode(msg.get("From", "")),
                    "subject": _decode(msg.get("Subject", "")),
                    "snippet": "",
                }
            )
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
    return mails


class ImapPoller:
    """Tick-cadenced IMAP poll → ``channel_message`` triage cards."""

    def __init__(
        self,
        engine: Any,
        fetcher: Callable[[], list[dict[str, Any]]] | None = None,
        poll_every_ticks: int | None = None,
    ):
        self._engine = engine
        self._fetcher = fetcher
        settings = engine.settings
        self.poll_every_ticks = max(
            1,
            int(
                poll_every_ticks
                if poll_every_ticks is not None
                else getattr(settings, "email_poll_every_ticks", 12)
            ),
        )
        self._tick_calls = 0
        self.last_poll_at: str | None = None

    @property
    def configured(self) -> bool:
        s = self._engine.settings
        return bool(
            getattr(s, "email_imap_host", "") and getattr(s, "email_imap_user", "")
        )

    # ------------------------------------------------------------------
    # Ticker action (registered by the engine when configured)
    # ------------------------------------------------------------------

    def tick_action(self) -> list[str]:
        """Poll every Nth call; cheap no-op otherwise."""
        self._tick_calls += 1
        if (self._tick_calls - 1) % self.poll_every_ticks:
            return []
        created = self.poll_once()
        return [f"email_poll:{len(created)}"] if created else ["email_poll:0"]

    # ------------------------------------------------------------------
    # One poll pass (public for tests)
    # ------------------------------------------------------------------

    def poll_once(self) -> list[dict[str, Any]]:
        """Fetch mail, file one triage card per UNSEEN uid. Idempotent."""
        if not self.configured:
            return []
        from datetime import UTC, datetime

        mails = self._fetch()
        created: list[dict[str, Any]] = []
        for mail in mails:
            uid_key = self._uid_key(str(mail.get("uid", "")))
            if not uid_key or self._seen(uid_key):
                continue
            request = self._engine.approvals.create(
                ApprovalRequest(
                    action_type=ACTION_CHANNEL_MESSAGE,
                    # Triage framing (#13): the founder DECIDES what the
                    # team does with this message — never handles it.
                    summary=(
                        f"Triage: email from {mail.get('from', '?')} — "
                        f"{mail.get('subject', '(no subject)')}"
                    ),
                    payload={
                        "channel": "email",
                        "uid": mail.get("uid"),
                        "from": mail.get("from"),
                        "subject": mail.get("subject"),
                        "snippet": str(mail.get("snippet", ""))[:SNIPPET_CHARS],
                    },
                    requested_by="email_in",
                    severity="low",
                )
            )
            self._mark_seen(uid_key)
            created.append({"uid": mail.get("uid"), "approval_id": request.id})
        self.last_poll_at = datetime.now(UTC).isoformat()
        return created

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch(self) -> list[dict[str, Any]]:
        if self._fetcher is not None:
            return self._fetcher()
        s = self._engine.settings
        password = self._engine.credentials.get(VAULT_PASSWORD_KEY) or ""
        if not password:
            log.warning("email_in: no %s in the vault; skipping", VAULT_PASSWORD_KEY)
            return []
        try:
            return imap_fetch(
                host=s.email_imap_host,
                user=s.email_imap_user,
                password=password,
                folder=getattr(s, "email_imap_folder", "INBOX") or "INBOX",
            )
        except Exception:  # noqa: BLE001 — a flaky mailbox must not kill the tick
            log.exception("email_in: IMAP fetch failed")
            return []

    def _uid_key(self, uid: str) -> str:
        if not uid:
            return ""
        s = self._engine.settings
        folder = getattr(s, "email_imap_folder", "INBOX") or "INBOX"
        return f"{s.email_imap_host}/{s.email_imap_user}/{folder}/{uid}"

    def _seen(self, uid_key: str) -> bool:
        row = self._engine.db.execute(
            "SELECT 1 FROM channel_email_seen WHERE uid_key = ?", (uid_key,)
        ).fetchone()
        return row is not None

    def _mark_seen(self, uid_key: str) -> None:
        self._engine.db.execute(
            "INSERT OR IGNORE INTO channel_email_seen (uid_key) VALUES (?)",
            (uid_key,),
        )
        self._engine.db.commit()


__all__ = [
    "ACTION_CHANNEL_MESSAGE",
    "VAULT_PASSWORD_KEY",
    "ImapPoller",
    "imap_fetch",
]
