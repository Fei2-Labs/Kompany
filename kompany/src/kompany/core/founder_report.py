"""Periodic founder reports (09-26-autopilot-reports).

The ticker calls :func:`report_tick` every tick; it is date-gated so one
daily report is written after the day's first tick and one weekly report
after Monday's first tick. Numbers come from the database (no LLM); the
narrative is one economy-tier call that is allowed to fail — a report
with a deterministic fallback narrative still ships. Delivery goes
through the auto notifier (Telegram when configured, otherwise dry-run)
and the outcome is stored on the row.

``kompany report`` / ``GET /reports`` / MCP ``kompany_reports`` read the
same rows; ``generate_report(period="manual")`` answers "give me a report
now" from any surface.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from kompany.state.ui_preferences import _read_config, _write_config

log = logging.getLogger(__name__)

ACTION_TYPE = "founder_report"
NARRATIVE_MAX_WORDS = {"daily": 180, "weekly": 350, "manual": 220}
NARRATIVE_MAX_TOKENS = 900
HOURS = {"daily": 24, "weekly": 24 * 7, "manual": 24}
LAST_DAILY_KEY = "autopilot.report.last_daily"
LAST_WEEKLY_KEY = "autopilot.report.last_weekly"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sqlite_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _count(db: Any, sql: str, params: tuple = ()) -> int:
    row = db.execute(sql, params).fetchone()
    return int(row["n"] or 0) if row else 0


def _rows(db: Any, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in db.execute(sql, params).fetchall()]
    except Exception:  # noqa: BLE001 — one missing table must not kill the report
        return []


# ---------------------------------------------------------------------------
# Data (pure reads)
# ---------------------------------------------------------------------------


def gather_report_data(engine: Any, hours: int) -> dict[str, Any]:
    """Deterministic snapshot of the last ``hours`` — no LLM, no writes."""
    db = engine.db
    now = _utcnow()
    since = _sqlite_ts(now - timedelta(hours=hours))
    data: dict[str, Any] = {
        "window_hours": hours,
        "since": since,
        "until": _sqlite_ts(now),
    }
    data["tasks"] = {
        r["status"]: int(r["n"])
        for r in _rows(
            db,
            "SELECT status, COUNT(*) AS n FROM tasks "
            "WHERE replace(updated_at, 'T', ' ') > ? GROUP BY status",
            (since,),
        )
    }
    data["tasks_done"] = [
        {"title": r["title"], "status": r["status"], "agent": r["assigned_agent"]}
        for r in _rows(
            db,
            "SELECT title, status, assigned_agent FROM tasks "
            "WHERE status IN ('completed', 'delivered', 'failed') "
            "AND replace(updated_at, 'T', ' ') > ? "
            "ORDER BY updated_at DESC LIMIT 15",
            (since,),
        )
    ]
    try:
        active = engine.projects.list_active()
        data["active_projects"] = [
            {"id": p.id, "name": p.name, "status": p.status.value}
            for p in active
        ]
    except Exception:  # noqa: BLE001
        data["active_projects"] = []
    data["ledger"] = [
        {
            "category": r["category"],
            "entries": int(r["n"]),
            "net": round(float(r["total"] or 0.0), 2),
        }
        for r in _rows(
            db,
            "SELECT category, COUNT(*) AS n, SUM(amount) AS total FROM ledger "
            "WHERE replace(timestamp, 'T', ' ') > ? GROUP BY category",
            (since,),
        )
    ]
    try:
        data["balance"] = round(float(engine.ledger.get_balance()), 2)
    except Exception:  # noqa: BLE001
        data["balance"] = None
    try:
        data["ai_spend_window"] = round(
            float(engine.ledger.spent_in_window(days=max(1, hours // 24))), 2
        )
    except Exception:  # noqa: BLE001
        data["ai_spend_window"] = None
    data["health_events_new"] = {
        r["kind"]: int(r["n"])
        for r in _rows(
            db,
            "SELECT kind, COUNT(*) AS n FROM health_events "
            "WHERE replace(created_at, 'T', ' ') > ? GROUP BY kind",
            (since,),
        )
    }
    data["health_events_open"] = _count(
        db, "SELECT COUNT(*) AS n FROM health_events WHERE status = 'open'"
    )
    try:
        approvals = engine.list_approvals()
        data["pending_approvals"] = [
            {"id": a.get("id"), "summary": a.get("summary")} for a in approvals
        ]
    except Exception:  # noqa: BLE001
        data["pending_approvals"] = []
    data["approvals_decided"] = {
        r["status"]: int(r["n"])
        for r in _rows(
            db,
            "SELECT status, COUNT(*) AS n FROM approval_requests "
            "WHERE status != 'pending' AND replace(created_at, 'T', ' ') > ? "
            "GROUP BY status",
            (since,),
        )
    }
    data["evolution_proposals"] = {
        r["status"]: int(r["n"])
        for r in _rows(
            db,
            "SELECT status, COUNT(*) AS n FROM artifact_proposals "
            "WHERE replace(created_at, 'T', ' ') > ? GROUP BY status",
            (since,),
        )
    }
    data["probations"] = {
        r["status"]: int(r["n"])
        for r in _rows(
            db,
            "SELECT status, COUNT(*) AS n FROM artifact_probations "
            "WHERE replace(coalesce(decided_at, started_at), 'T', ' ') > ? GROUP BY status",
            (since,),
        )
    }
    data["debates"] = _count(
        db,
        "SELECT COUNT(*) AS n FROM debates WHERE replace(created_at, 'T', ' ') > ?",
        (since,),
    )
    data["distillations"] = _count(
        db,
        "SELECT COUNT(*) AS n FROM audit_log WHERE event_type = 'learning.distillation_run' "
        "AND replace(timestamp, 'T', ' ') > ?",
        (since,),
    )
    data["memories_written"] = _count(
        db,
        "SELECT COUNT(*) AS n FROM agent_memories "
        "WHERE replace(created_at, 'T', ' ') > ?",
        (since,),
    )
    try:
        from kompany.core.artifact_evolution.flip_stats import flip_rates

        data["flip_rates"] = flip_rates(engine)
    except Exception:  # noqa: BLE001
        data["flip_rates"] = None
    try:
        runtime = engine.runtime.get() or {}
        data["runtime_state"] = runtime.get("state")
    except Exception:  # noqa: BLE001
        data["runtime_state"] = None
    return data


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------


def _digest_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    tasks = data.get("tasks") or {}
    if tasks:
        lines.append(
            "tasks: " + ", ".join(f"{k} {v}" for k, v in sorted(tasks.items()))
        )
    for t in (data.get("tasks_done") or [])[:8]:
        lines.append(f"- {t['status']}: {t['title']} ({t['agent']})")
    if data.get("active_projects"):
        lines.append(
            "active projects: "
            + ", ".join(p["name"] for p in data["active_projects"][:8])
        )
    for entry in data.get("ledger") or []:
        lines.append(
            f"ledger {entry['category']}: {entry['entries']} entries, net {entry['net']:+.2f}"
        )
    if data.get("balance") is not None:
        lines.append(f"balance: {data['balance']:.2f}")
    if data.get("ai_spend_window") is not None:
        lines.append(f"ai spend in window: {data['ai_spend_window']:.2f}")
    for kind, n in (data.get("health_events_new") or {}).items():
        lines.append(f"health event {kind}: x{n}")
    lines.append(f"open health events: {data.get('health_events_open', 0)}")
    pend = data.get("pending_approvals") or []
    if pend:
        lines.append(
            f"waiting on founder ({len(pend)}): "
            + "; ".join(str(p.get("summary") or p.get("id")) for p in pend[:5])
        )
    if data.get("evolution_proposals"):
        lines.append(
            "evolution proposals: "
            + ", ".join(f"{k} {v}" for k, v in data["evolution_proposals"].items())
        )
    if data.get("probations"):
        lines.append(
            "soul probations: "
            + ", ".join(f"{k} {v}" for k, v in data["probations"].items())
        )
    if data.get("debates"):
        lines.append(f"debates held: {data['debates']}")
    if data.get("distillations"):
        lines.append(
            f"distillation runs: {data['distillations']}, memories written: "
            f"{data.get('memories_written', 0)}"
        )
    fr = data.get("flip_rates") or {}
    if fr.get("rubber_stamp"):
        lines.append("WARNING: evolution rubber-stamp — nothing has been rejected or reverted")
    if data.get("runtime_state") and data["runtime_state"] != "running":
        lines.append(f"runtime state: {data['runtime_state']}")
    return lines


def fallback_narrative(period: str, data: dict[str, Any]) -> str:
    lines = _digest_lines(data)
    head = f"{period.capitalize()} report ({data.get('window_hours')}h)"
    if not lines:
        return f"{head}: a quiet period; nothing notable."
    return head + "\n" + "\n".join(lines)


def build_narrative(engine: Any, period: str, data: dict[str, Any]) -> tuple[str, float]:
    """One economy-tier call; on any failure return the deterministic text."""
    digest = "\n".join(_digest_lines(data)) or "- a quiet period; nothing notable."
    settings = engine.settings
    company = getattr(settings, "company_name", "") or "Kompany"
    max_words = NARRATIVE_MAX_WORDS.get(period, 200)
    try:
        resp = engine.llm.call(
            model=settings.get_model_for_tier("economy"),
            system=(
                f"You are the Chief of Staff of {company}, writing the "
                f"founder's {period} report. Plain language, no hype. Use "
                "ONLY the facts given; never invent numbers or events. Lead "
                "with what matters most; end with what (if anything) needs "
                "the founder."
            ),
            prompt=(
                f"Write the {period} report in at most {max_words} words. "
                f"Facts for the window ({data.get('window_hours')}h):\n{digest}\n\n"
                "Report only — no headers, no preamble."
            ),
            agent_name="CoS",
            action_type=ACTION_TYPE,
            max_tokens=NARRATIVE_MAX_TOKENS,
        )
        text = (getattr(resp, "text", "") or "").strip()
        cost = float(getattr(resp, "cost_usd", 0.0) or 0.0)
        if text:
            return text, cost
    except Exception:  # noqa: BLE001 — narrative is a nicety; the report still ships
        log.exception("founder report narrative failed; using fallback")
    return fallback_narrative(period, data), 0.0


# ---------------------------------------------------------------------------
# Generate + deliver
# ---------------------------------------------------------------------------


def _disabled(value: Any) -> bool:
    """``off`` / ``none`` / ``false`` all mean disabled (YAML parses a bare
    ``off`` as boolean False, so the string form is not the only shape)."""
    return str(value).strip().lower() in {"off", "none", "false", "0", ""}


def _deliver(engine: Any, report: dict[str, Any]) -> list[dict[str, Any]]:
    settings = engine.settings
    if _disabled(getattr(settings, "founder_report_delivery", "auto")):
        return []
    period = report["period"]
    pend = len(report.get("data", {}).get("pending_approvals") or [])
    event = {
        "kind": f"founder_report_{period}",
        "severity": "action_required" if pend else "info",
        "summary": f"{period.capitalize()} report\n\n{report['narrative']}",
        "payload": {"report_id": report["id"], "pending_approvals": pend},
    }
    try:
        return engine.dispatch_notifications([event], adapter="auto")
    except Exception as exc:  # noqa: BLE001 — delivery failure is recorded, never raised
        log.exception("founder report delivery failed")
        return [{"adapter": "auto", "status": "failed", "error": type(exc).__name__}]


def generate_report(engine: Any, period: str = "manual", deliver: bool = True) -> dict[str, Any]:
    hours = HOURS.get(period, 24)
    data = gather_report_data(engine, hours)
    narrative, cost = build_narrative(engine, period, data)
    report = engine.founder_reports.record(
        period=period,
        period_start=data["since"],
        period_end=data["until"],
        narrative=narrative,
        data=data,
        cost=cost,
    )
    delivery: list[dict[str, Any]] = []
    if deliver:
        delivery = _deliver(engine, report)
        engine.founder_reports.set_delivery(report["id"], delivery)
        report["delivery"] = delivery
    engine.audit.record(
        f"{ACTION_TYPE}.generated",
        f"{period.capitalize()} founder report generated",
        detail={
            "report_id": report["id"],
            "period": period,
            "cost_usd": cost,
            "delivered": [d.get("status") for d in delivery],
        },
    )
    return report


def report_tick(engine: Any) -> list[str]:
    """Ticker step: date-gated daily + weekly reports."""
    cadence = str(getattr(engine.settings, "founder_report_cadence", "daily_plus_weekly"))
    if _disabled(cadence):
        return []
    db = engine.db
    today = _utcnow().date()
    actions: list[str] = []
    want_daily = cadence in ("daily", "daily_plus_weekly")
    want_weekly = cadence in ("weekly", "daily_plus_weekly")
    if want_weekly and today.weekday() == 0:
        last = _read_config(db, LAST_WEEKLY_KEY)
        if last != today.isoformat():
            rep = generate_report(engine, "weekly")
            _write_config(db, LAST_WEEKLY_KEY, today.isoformat())
            db.commit()
            actions.append(f"founder_report:weekly:{rep['id']}")
    if want_daily:
        last = _read_config(db, LAST_DAILY_KEY)
        if last != today.isoformat():
            rep = generate_report(engine, "daily")
            _write_config(db, LAST_DAILY_KEY, today.isoformat())
            db.commit()
            actions.append(f"founder_report:daily:{rep['id']}")
    return actions


def reports_list(engine: Any, period: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    return engine.founder_reports.list(period=period, limit=limit)


def report_latest(engine: Any, period: str = "daily") -> dict[str, Any] | None:
    return engine.founder_reports.latest(period)


__all__ = [
    "ACTION_TYPE",
    "fallback_narrative",
    "gather_report_data",
    "generate_report",
    "report_latest",
    "report_tick",
    "reports_list",
]
