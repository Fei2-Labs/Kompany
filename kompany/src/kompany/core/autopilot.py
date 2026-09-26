"""Autopilot wiring (09-26-autopilot-reports).

One call from the engine constructor registers the ticker steps that make
the company run without a human starting anything: automatic
distillation, automatic evolution proposals, the probation gate on
evolved souls, and periodic founder reports. Logic lives in ``core/autopilot_learning.py`` and
``core/founder_report.py``; this module only appends the steps (engine.py
is over the ADR-0003 size cap).

Order: after ``update`` (release check) and before the anima steps, so
the report of a day includes whatever the earlier steps of that same
tick produced.
"""

from __future__ import annotations

from typing import Any

from kompany.core.artifact_evolution.probation import probation_tick
from kompany.core.autopilot_learning import distill_tick, evolution_tick
from kompany.core.founder_report import report_tick
from kompany.state.artifact_probations import ArtifactProbationStore
from kompany.state.founder_reports import FounderReportStore

STEP_NAMES: tuple[str, ...] = (
    "autopilot_distill",
    "autopilot_evolution",
    "evolution_probation",
    "founder_report",
)


def install(engine: Any) -> None:
    engine.founder_reports = FounderReportStore(engine.db)
    engine.artifact_probations = ArtifactProbationStore(engine.db)
    engine.ticker.actions.extend(
        [
            ("autopilot_distill", lambda: distill_tick(engine)),
            ("autopilot_evolution", lambda: evolution_tick(engine)),
            ("evolution_probation", lambda: probation_tick(engine)),
            ("founder_report", lambda: report_tick(engine)),
        ]
    )


__all__ = ["STEP_NAMES", "install"]
