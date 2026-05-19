"""Company targets — quantitative onboarding contract.

A *target* is a number the founder commits to during onboarding so the
team's later decisions (CEO directive routing, CFO budget checks, CoS
retrospectives) and the resilience watchdog can reason against it. The
four primitives are:

* ``initial_budget``    — starting cash in USD
* ``revenue_target``    — revenue goal in USD
* ``customer_target``   — paying-customer count (optional)
* ``deadline``          — ISO 8601 UTC timestamp by which the targets
  are evaluated

Targets live in three states tracked by the ``source`` field:

* ``founder``        — the founder's original numbers from onboarding
* ``team_proposal``  — what CEO+CFO+CoS recommend after a feasibility
  review
* ``agreed``         — the founder's final pick after weighing the
  proposal (this is what every downstream consumer reads)

Storage is the existing ``company_config`` key-value table — no new
schema migration is needed. Each state is serialised as one
``targets.<source>`` row whose value is the JSON blob of the model.

``CompanyTargets`` uses ``extra="forbid"`` so typo'd keys fail loudly at
parse time. The deadline validator parses through ``datetime.fromisoformat``
and re-emits canonical ISO 8601 so the column always round-trips.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kompany.state.database import Database


TargetSource = Literal["founder", "team_proposal", "agreed"]


# Keys we own in ``company_config``. Kept module-level so callers can
# clear or migrate them without re-discovering the key naming scheme.
_KEY_FOUNDER = "targets.founder"
_KEY_PROPOSAL = "targets.team_proposal"
_KEY_AGREED = "targets.agreed"
_KEY_REVIEW_THREAD = "targets.review_thread_id"

# Legacy single-value keys retained for backward-compat reads. The
# ``initial_budget`` row predates this module (templates write it) so the
# loader merges it into the founder snapshot when the explicit
# ``targets.founder`` row is missing.
_LEGACY_KEY_INITIAL_BUDGET = "initial_budget"
_LEGACY_KEY_REVENUE_TARGET = "revenue_target"
_LEGACY_KEY_CUSTOMER_TARGET = "customer_target"
_LEGACY_KEY_DEADLINE = "deadline"


class CompanyTargets(BaseModel):
    """One snapshot of the four quantitative targets.

    Construction examples::

        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=10000.0,
            customer_target=50,
            deadline="2026-08-19T00:00:00+00:00",
            source="founder",
        )

    ``customer_target`` is optional (``None``) because some business
    models (e.g. consulting on a single retainer) are revenue-only.
    """

    model_config = ConfigDict(extra="forbid")

    initial_budget: float = Field(default=0.0, ge=0.0)
    revenue_target: float = Field(default=0.0, ge=0.0)
    customer_target: int | None = Field(default=None, ge=0)
    deadline: str | None = None
    source: TargetSource = "founder"

    @field_validator("deadline")
    @classmethod
    def _validate_iso(cls, value: str | None) -> str | None:
        """Reject obviously-bad deadline strings; round-trip the rest.

        Empty string normalises to ``None`` so REST/web form callers can
        send ``""`` for "no deadline" without us writing a literal empty
        row.
        """
        if value is None or value == "":
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"deadline {value!r} is not a valid ISO 8601 timestamp"
            ) from exc
        # Round-trip via isoformat so callers always see a canonical form.
        return parsed.isoformat()


class TargetsBundle(BaseModel):
    """All three target states + the review approval thread id.

    Used by the episode payload and by ``engine.get_targets_bundle()``
    so a single read returns the entire negotiation trace.
    """

    model_config = ConfigDict(extra="forbid")

    founder: CompanyTargets
    proposal: CompanyTargets | None = None
    agreed: CompanyTargets | None = None
    review_thread_id: str | None = None


# ---------------------------------------------------------------------------
# Service: get / set / clear
# ---------------------------------------------------------------------------


def _read_config(db: Database, key: str) -> str | None:
    row = db.execute(
        "SELECT value FROM company_config WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def _write_config(db: Database, key: str, value: str) -> None:
    db.execute(
        """INSERT INTO company_config (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET
             value = excluded.value,
             updated_at = datetime('now')""",
        (key, value),
    )


def _delete_config(db: Database, key: str) -> None:
    db.execute("DELETE FROM company_config WHERE key = ?", (key,))


def _read_float(db: Database, key: str) -> float | None:
    raw = _read_config(db, key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _read_int(db: Database, key: str) -> int | None:
    raw = _read_config(db, key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _read_legacy_founder(db: Database) -> CompanyTargets | None:
    """Reconstruct a founder snapshot from the legacy flat keys.

    Used as fallback before the first explicit ``targets.founder`` row
    exists — that way templates that wrote ``initial_budget`` directly
    keep producing a usable ``get_targets`` result.
    """
    initial_budget = _read_float(db, _LEGACY_KEY_INITIAL_BUDGET)
    revenue_target = _read_float(db, _LEGACY_KEY_REVENUE_TARGET)
    customer_target = _read_int(db, _LEGACY_KEY_CUSTOMER_TARGET)
    deadline = _read_config(db, _LEGACY_KEY_DEADLINE)
    # Only fabricate a record when at least one value is present —
    # otherwise return None so callers can pick their own default.
    if (
        initial_budget is None
        and revenue_target is None
        and customer_target is None
        and (deadline is None or deadline == "")
    ):
        return None
    return CompanyTargets(
        initial_budget=initial_budget or 0.0,
        revenue_target=revenue_target or 0.0,
        customer_target=customer_target,
        deadline=deadline or None,
        source="founder",
    )


def get_targets(db: Database) -> CompanyTargets:
    """Return the authoritative targets for agent + watchdog reads.

    Resolution order: ``agreed`` > ``founder`` > legacy flat keys > all
    zeros. Always returns a model — callers never have to handle
    ``None``.
    """
    raw_agreed = _read_config(db, _KEY_AGREED)
    if raw_agreed:
        try:
            return CompanyTargets.model_validate_json(raw_agreed)
        except Exception:
            # Corrupt row — fall through to the founder state.
            pass
    raw_founder = _read_config(db, _KEY_FOUNDER)
    if raw_founder:
        try:
            return CompanyTargets.model_validate_json(raw_founder)
        except Exception:
            pass
    legacy = _read_legacy_founder(db)
    if legacy is not None:
        return legacy
    return CompanyTargets(source="founder")


def get_state(db: Database, source: TargetSource) -> CompanyTargets | None:
    """Return one specific state (``founder`` / ``team_proposal`` /
    ``agreed``) or ``None`` if it hasn't been written yet."""
    key = {
        "founder": _KEY_FOUNDER,
        "team_proposal": _KEY_PROPOSAL,
        "agreed": _KEY_AGREED,
    }[source]
    raw = _read_config(db, key)
    if raw is None:
        if source == "founder":
            return _read_legacy_founder(db)
        return None
    try:
        return CompanyTargets.model_validate_json(raw)
    except Exception:
        return None


def get_bundle(db: Database) -> TargetsBundle:
    """Return all three states + review thread id in one read."""
    founder = get_state(db, "founder") or CompanyTargets(source="founder")
    proposal = get_state(db, "team_proposal")
    agreed = get_state(db, "agreed")
    review_thread_id = _read_config(db, _KEY_REVIEW_THREAD) or None
    return TargetsBundle(
        founder=founder,
        proposal=proposal,
        agreed=agreed,
        review_thread_id=review_thread_id,
    )


def set_targets(db: Database, targets: CompanyTargets) -> CompanyTargets:
    """Persist a target snapshot keyed by its ``source``.

    Returns the same model (round-tripped) so callers can use the return
    value directly without a second read.
    """
    key = {
        "founder": _KEY_FOUNDER,
        "team_proposal": _KEY_PROPOSAL,
        "agreed": _KEY_AGREED,
    }[targets.source]
    payload = targets.model_dump_json()
    _write_config(db, key, payload)
    # Mirror founder-state numbers onto the legacy flat keys so older
    # readers (template service, episode mission resolver) see the same
    # values.
    if targets.source == "founder":
        _write_config(db, _LEGACY_KEY_INITIAL_BUDGET, str(targets.initial_budget))
        _write_config(db, _LEGACY_KEY_REVENUE_TARGET, str(targets.revenue_target))
        if targets.customer_target is not None:
            _write_config(
                db, _LEGACY_KEY_CUSTOMER_TARGET, str(targets.customer_target)
            )
        else:
            _delete_config(db, _LEGACY_KEY_CUSTOMER_TARGET)
        if targets.deadline:
            _write_config(db, _LEGACY_KEY_DEADLINE, targets.deadline)
        else:
            _delete_config(db, _LEGACY_KEY_DEADLINE)
    db.commit()
    return targets


def set_review_thread_id(db: Database, approval_id: str | None) -> None:
    """Record the approval_request id that carries the feasibility review."""
    if approval_id is None:
        _delete_config(db, _KEY_REVIEW_THREAD)
    else:
        _write_config(db, _KEY_REVIEW_THREAD, approval_id)
    db.commit()


def clear_targets(db: Database) -> None:
    """Wipe every targets-owned row. Used by tests; not exposed via UI."""
    for key in (
        _KEY_FOUNDER,
        _KEY_PROPOSAL,
        _KEY_AGREED,
        _KEY_REVIEW_THREAD,
        _LEGACY_KEY_INITIAL_BUDGET,
        _LEGACY_KEY_REVENUE_TARGET,
        _LEGACY_KEY_CUSTOMER_TARGET,
        _LEGACY_KEY_DEADLINE,
    ):
        _delete_config(db, key)
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compose_summary(
    targets: CompanyTargets,
    *,
    now: datetime | None = None,
    cash: float | None = None,
) -> str:
    """Render a one-paragraph human-readable summary.

    Injected into the system prompt of CEO classify / CFO budget check /
    CoS retrospect calls so each agent reasons against the agreed
    numbers. Designed to be terse — we want the agent to absorb the
    numbers, not parse a wall of text.
    """
    parts: list[str] = []
    if targets.revenue_target > 0:
        parts.append(f"revenue target ${targets.revenue_target:,.0f}")
    if targets.customer_target is not None:
        parts.append(f"customer target {targets.customer_target}")
    if targets.initial_budget > 0:
        parts.append(f"initial budget ${targets.initial_budget:,.0f}")
    if cash is not None:
        parts.append(f"cash on hand ${cash:,.2f}")
    if targets.deadline:
        try:
            dl = datetime.fromisoformat(targets.deadline)
        except (TypeError, ValueError):
            dl = None
        if dl is not None:
            if now is None:
                # Match dl's tz-awareness so subtraction never raises.
                if dl.tzinfo is not None:
                    from datetime import timezone

                    now = datetime.now(timezone.utc).astimezone(dl.tzinfo)
                else:
                    now = datetime.now()
            try:
                hours_remaining = (dl - now).total_seconds() / 3600.0
            except TypeError:
                hours_remaining = None
            if hours_remaining is not None:
                days = hours_remaining / 24.0
                parts.append(
                    f"deadline {targets.deadline} ({days:.1f} days remaining)"
                )
            else:
                parts.append(f"deadline {targets.deadline}")
    if not parts:
        return "Company targets: none set."
    return "Company targets: " + "; ".join(parts) + "."


__all__ = [
    "CompanyTargets",
    "TargetSource",
    "TargetsBundle",
    "clear_targets",
    "compose_summary",
    "get_bundle",
    "get_state",
    "get_targets",
    "set_review_thread_id",
    "set_targets",
]
