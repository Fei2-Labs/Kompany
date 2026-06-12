"""Channel engine ops (06-12-channels PRD D5).

Single logic home for ``channels_status`` / ``outbox_list`` — CLI,
REST, MCP, and SDK all flatten through these (interfaces parity rule).
"""

from __future__ import annotations

from typing import Any

from kompany.channels.outbox import OutboxStore


def channels_status_op(engine: Any) -> dict[str, Any]:
    """Adapter health + outbox counts in one canonical dict."""
    settings = engine.settings
    worker = getattr(engine, "telegram_worker", None)
    poller = getattr(engine, "email_poller", None)
    return {
        "telegram": {
            "configured": bool(getattr(settings, "telegram_bot_token", "")),
            "running": bool(worker is not None and worker.running),
            "last_update_at": getattr(worker, "last_update_at", None),
            "updates_handled": int(getattr(worker, "updates_handled", 0) or 0),
        },
        "email": {
            "configured": bool(
                getattr(settings, "email_imap_host", "")
                and getattr(settings, "email_imap_user", "")
            ),
            "poll_every_ticks": int(
                getattr(settings, "email_poll_every_ticks", 12)
            ),
            "last_poll_at": getattr(poller, "last_poll_at", None),
        },
        "outbox": {
            "enabled": bool(getattr(settings, "anima_outbox_enabled", False)),
            "counts": OutboxStore(engine.db).counts_by_status(),
        },
    }


def outbox_list_op(engine: Any, limit: int = 20) -> list[dict[str, Any]]:
    """Most recent outbox rows, newest first."""
    return OutboxStore(engine.db).list(limit=limit)


__all__ = ["channels_status_op", "outbox_list_op"]
