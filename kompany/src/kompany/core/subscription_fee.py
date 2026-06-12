"""Monthly ModelSource subscription fee as a recurring ledger expense.

06-11-harness-execution-leg D2: under subscription billing the monthly
fee IS the real recurring expense (per-call cost goes to the shadow
record). ``engine.heartbeat_once`` calls :func:`book_subscription_fee_if_due`
on every tick; idempotency keys on the current calendar month embedded in
the ledger description — checked via ledger query, never a flag file — so
engine restarts / repeated heartbeats book at most one fee per month.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from kompany.state.models import LedgerCategory, NotificationEvent


def _utcnow() -> datetime:
    """Injectable clock — tests freeze time by monkeypatching this."""
    return datetime.now(UTC)


def book_subscription_fee_if_due(
    settings: Any,
    db: Any,
    ledger: Any,
) -> NotificationEvent | None:
    """Book the monthly subscription fee, at most once per calendar month.

    No-op (returns ``None``) when no ModelSource is configured, when the
    active source bills per-token (api mode), or when this month's fee is
    already on the ledger. Returns a notification event when a fee was
    booked so the heartbeat can surface it.
    """
    source = getattr(settings, "model_source", None)
    if source is None or source.billing_mode != "subscription":
        return None
    month = _utcnow().strftime("%Y-%m")
    existing = db.execute(
        "SELECT 1 FROM ledger WHERE category = ? AND description LIKE ? "
        "LIMIT 1",
        (LedgerCategory.SUBSCRIPTION_FEE.value, f"%{month}%"),
    ).fetchone()
    if existing is not None:
        return None
    fee = float(source.monthly_fee_usd or 0.0)
    ledger.record(
        amount=-abs(fee),
        description=f"Subscription fee: {source.kind} {month}",
        category=LedgerCategory.SUBSCRIPTION_FEE,
        approved_by="auto",
    )
    return NotificationEvent(
        kind="subscription_fee_booked",
        severity="info",
        summary=(
            f"Monthly subscription fee booked: ${fee:.2f} "
            f"({source.kind}, {month})"
        ),
        payload={
            "source_kind": source.kind,
            "month": month,
            "amount_usd": fee,
        },
    )


__all__ = ["book_subscription_fee_if_due"]
