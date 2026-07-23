"""Transport-neutral context for channel directives."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DirectiveContext:
    """Identity and isolation boundary attached to an inbound directive."""

    channel: str
    account_id: str
    chat_id: str
    sender_id: str
    company_id: str = "default"
    project_id: str | None = None
    thread_id: str | None = None
    active_agent_id: str | None = None
    session_epoch: int | None = None

    @property
    def session_key(self) -> str:
        """Return a stable key for this virtual channel session."""
        if (
            self.channel == "telegram"
            and self.company_id == "default"
            and self.account_id == "default"
            and not self.thread_id
            and not self.sender_id
            and self.project_id is None
            and self.active_agent_id in (None, "ceo")
            and self.session_epoch in (None, 0)
        ):
            return self.chat_id
        identity = json.dumps(
            {
                "company_id": self.company_id,
                "channel": self.channel,
                "account_id": self.account_id,
                "chat_id": self.chat_id,
                "thread_id": self.thread_id,
                "sender_id": self.sender_id,
                "project_id": self.project_id,
                "active_agent_id": self.active_agent_id,
                "session_epoch": self.session_epoch,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"context:{digest}"
