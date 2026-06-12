"""Canonical company-status snapshot shared by all four interfaces.

Before 06-12-daemon-tick-loop PR2 each interface (CLI, REST, MCP
dispatch, SDK) hand-built its own ``kompany_status`` dict, which had
already drifted (only REST carried the virtual-time fields). This
module is now the single builder — interfaces call
:func:`build_status` and render, so parity is structural
(interfaces spec: same input → same observable result).

The dict is additive-only (Output Stability Rule): new keys are fine,
existing keys never change meaning or disappear.
"""

from __future__ import annotations

from typing import Any


def ticker_block(engine: Any) -> dict[str, Any]:
    """Ticker visibility block for status surfaces (PRD D5).

    Defensive against engine fakes without a ticker (interface tests):
    a missing ticker reports as not running with zeroed counters.
    """
    ticker = getattr(engine, "ticker", None)
    if ticker is None:
        return {
            "running": False,
            "last_tick_at": None,
            "tick_count": 0,
            "interval_seconds": 0,
        }
    return {
        "running": bool(ticker.running),
        "last_tick_at": ticker.last_tick_at,
        "tick_count": int(ticker.tick_count),
        "interval_seconds": int(ticker.tick_interval_seconds),
    }


def build_status(engine: Any) -> dict[str, Any]:
    """Build the company status dict (``kompany status`` / ``GET /status``)."""
    from kompany.state import virtual_clock

    cfo = engine.registry.get("cfo")
    summary = cfo.get_summary()
    active = engine.projects.list_active()
    # Virtual clock fields default to 0 if the engine fake (in unit
    # tests) doesn't expose a db handle.
    try:
        vd_elapsed = virtual_clock.get_elapsed(engine.db)
        vd_budget = virtual_clock.get_budget(engine.db)
    except AttributeError:
        vd_elapsed = 0
        vd_budget = 0
    vd_remaining = max(0, vd_budget - vd_elapsed) if vd_budget > 0 else 0
    return {
        "company": engine.settings.company_name,
        "goal": engine.settings.company_goal,
        "time_horizon": engine.settings.company_time_horizon,
        "exclusions": engine.settings.company_exclusions,
        "stage": engine.settings.company_stage,
        "balance": summary["balance"],
        "total_income": summary["total_income"],
        "total_expenses": summary["total_expenses"],
        "total_ai_costs": abs(summary["total_ai_costs"]),
        "active_projects": len(active),
        # Virtual time (model D — 1 completed task = 1 virtual day).
        # The dashboard's days/burn surface this, not wall time, so a
        # paused Kompany doesn't lose runway and a productive team
        # doesn't get penalised by clock drift.
        "virtual_days_elapsed": vd_elapsed,
        "virtual_days_budget": vd_budget,
        "virtual_days_remaining": vd_remaining,
        # Daemon tick loop visibility (06-12 D5): the ticker joins the
        # existing status operation instead of growing a new one.
        "ticker": ticker_block(engine),
    }


__all__ = ["build_status", "ticker_block"]
