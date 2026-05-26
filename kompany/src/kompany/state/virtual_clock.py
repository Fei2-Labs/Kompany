"""Virtual time accounting (model D: task-completion driven).

The Kompany simulation runs in virtual time, not wall time. One
virtual day = one completed task. This decouples runway / burn from
real-world hours so:

* A founder who pauses Kompany.app overnight doesn't lose virtual days.
* An LLM call that took 30s of wall time vs 3s of wall time both count
  the same: 1 virtual day per task completed.
* The dashboard's ``days: 12/89`` is honest — it tracks how much
  *work* the team has done against the deadline budget, not how many
  real days have elapsed.

Two pieces of state live in ``company_config``:

* ``virtual_day_counter`` — integer, increments on every task complete.
* ``virtual_days_budget`` — integer snapshot of "days between template-
  apply and the founder's ISO deadline." Computed once, then frozen
  even as the real ISO date drifts.

The budget is computed lazily on first call to ``get_budget`` if a
``targets.agreed.deadline`` exists — that way installs that pre-date
this module pick it up automatically without a migration.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any


_KEY_COUNTER = "virtual_day_counter"
_KEY_BUDGET = "virtual_days_budget"
_KEY_AGREED = "targets.agreed"


def _read_int(db, key: str) -> int | None:
    row = db.execute(
        "SELECT value FROM company_config WHERE key = ?", (key,)
    ).fetchone()
    if row is None or row["value"] is None:
        return None
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return None


def _write_int(db, key: str, value: int) -> None:
    db.execute(
        """INSERT INTO company_config (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET
             value = excluded.value,
             updated_at = excluded.updated_at""",
        (key, str(int(value))),
    )
    db.commit()


def get_elapsed(db) -> int:
    """How many virtual days the team has burned so far."""
    return _read_int(db, _KEY_COUNTER) or 0


def get_budget(db) -> int:
    """How many virtual days the founder budgeted between onboarding
    and their ISO deadline. Computed lazily on first access if
    ``targets.agreed`` exists but the budget hasn't been snapshotted
    yet."""
    cached = _read_int(db, _KEY_BUDGET)
    if cached is not None:
        return cached
    # Lazy snapshot from agreed targets.
    row = db.execute(
        "SELECT value FROM company_config WHERE key = ?", (_KEY_AGREED,)
    ).fetchone()
    if not row or not row["value"]:
        return 0
    try:
        payload = json.loads(row["value"])
    except Exception:
        return 0
    deadline_str = payload.get("deadline")
    if not deadline_str:
        return 0
    try:
        # Accept both date and full ISO datetime forms.
        if "T" in deadline_str:
            dl = datetime.fromisoformat(deadline_str.replace("Z", "+00:00")).date()
        else:
            dl = date.fromisoformat(deadline_str)
    except ValueError:
        return 0
    today = date.today()
    budget = max(0, (dl - today).days)
    _write_int(db, _KEY_BUDGET, budget)
    return budget


def tick(
    db,
    reason: str,
    *,
    detail: dict[str, Any] | None = None,
    audit=None,
    project_id: str | None = None,
) -> int:
    """Advance the virtual day counter by one.

    Returns the new counter value. Best-effort audit if an audit log
    instance is provided (project runner has one); otherwise the tick
    is silent on the timeline but still moves the counter.
    """
    current = get_elapsed(db)
    new_value = current + 1
    _write_int(db, _KEY_COUNTER, new_value)
    if audit is not None:
        audit.record(
            event_type="virtual_day.advanced",
            action=f"Virtual day +1 → {new_value} ({reason})",
            detail={
                "reason": reason,
                "previous": current,
                "current": new_value,
                **(detail or {}),
            },
            project_id=project_id,
        )
    return new_value


def reset(db) -> None:
    """Test helper — wipe both counter and budget snapshot."""
    db.execute(
        "DELETE FROM company_config WHERE key IN (?, ?)",
        (_KEY_COUNTER, _KEY_BUDGET),
    )
    db.commit()
