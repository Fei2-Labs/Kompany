"""Mandatory outward pre-flight pipeline (ADR-0008 #3).

Before the outward lane executes ANY unattended ``auto`` action, the engine
runs three gates IN ORDER, short-circuiting on the FIRST hold:

1. **ADR-0007 C-suite review** — reuses ``core/csuite_review`` (the
   adversarial reviewer logic) for the deliverable's ``REVIEW_ROLES``.
2. **De-AI / quality gate** — ``core/deai_gate.DeAIGate`` (rules + LLM
   judge). Only a ``'hold'`` verdict blocks; a ``'rewrite'`` is recorded
   but does not stop the pipeline (it is advisory, not a ship-blocker).
3. **No-fabrication check** — ``core/fabrication_check.FabricationCheck``
   against ``engine.get_company_state()`` + recent project episodes.

:func:`run_preflight` returns a :class:`PreflightResult`. ``ok`` is True
only when every gate passed (no hold). Any hold → ``ok=False`` plus the
holds; the caller parks the action for ``kompany approve``. The pipeline
never raises — a gate that cannot run fails CLOSED (records a hold).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kompany.core.deai_gate import HOLD, DeAIGate, GateResult
from kompany.core.deliverables import DeliverableClass, review_roles_for
from kompany.core.fabrication_check import FabricationCheck

# How many recent project episodes to pull as fabrication-check evidence.
_EPISODE_EVIDENCE_LIMIT = 20


@dataclass
class PreflightResult:
    """Aggregate outcome of the outward pre-flight pipeline.

    ``ok`` is True only when no gate held. ``holds`` is the list of
    per-gate :class:`GateResult` objects that returned a hold (in pipeline
    order; at most one because the pipeline short-circuits). ``passed`` is
    the list of gates that ran and passed before any short-circuit.
    """

    ok: bool
    holds: list[GateResult] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)


def run_preflight(engine: Any, action: Any) -> PreflightResult:
    """Run the three mandatory gates in order, short-circuiting on hold.

    ``action`` is duck-typed; the pipeline reads (via attribute or dict
    key): ``text`` (the outward copy), ``deliverable_class`` (a
    :class:`DeliverableClass` or its string value), and optionally
    ``task`` / ``project`` for the C-suite review. Missing pieces degrade
    gracefully — the gate runs with what it has.
    """
    text = _attr(action, "text", "") or ""
    cls = _resolve_class(_attr(action, "deliverable_class", None))
    passed: list[str] = []

    # 1. ADR-0007 C-suite review.
    review = _run_csuite_gate(engine, action, cls, text)
    if review.is_hold:
        return PreflightResult(ok=False, holds=[review], passed=passed)
    passed.append("csuite_review")

    # 2. De-AI / quality gate. Only HOLD blocks; REWRITE is advisory.
    deai = _run_deai_gate(engine, text)
    if deai.is_hold:
        return PreflightResult(ok=False, holds=[deai], passed=passed)
    passed.append("deai")

    # 3. No-fabrication check.
    fab = _run_fabrication_gate(engine, text)
    if fab.is_hold:
        return PreflightResult(ok=False, holds=[fab], passed=passed)
    passed.append("fabrication")

    return PreflightResult(ok=True, holds=[], passed=passed)


# ---------------------------------------------------------------------------
# Per-gate runners (each fails CLOSED)
# ---------------------------------------------------------------------------


def _run_csuite_gate(
    engine: Any, action: Any, cls: DeliverableClass, text: str
) -> GateResult:
    """Reuse the ADR-0007 reviewer logic for a synchronous pass/hold."""
    from kompany.core import csuite_review

    try:
        roles = review_roles_for(cls)
        if not roles:
            return GateResult(verdict="pass", findings=[], gate="csuite_review")
        task = _attr(action, "task", None) or _DeliverableShim(text)
        findings, any_hold, errored = csuite_review._run_review(
            engine, task, cls, roles, text
        )
        blocking = any_hold or errored or any(
            f.get("defects") for f in findings
        )
        defects = [
            f"[{f.get('role')}] {d}"
            for f in findings for d in (f.get("defects") or [])
        ]
        if errored and not defects:
            defects.append("C-suite review could not run — failing closed.")
        return GateResult(
            verdict=HOLD if blocking else "pass",
            findings=defects,
            gate="csuite_review",
        )
    except Exception as exc:  # noqa: BLE001 — review fails closed
        return GateResult(
            verdict=HOLD,
            findings=[f"C-suite review error (failing closed): {exc}"],
            gate="csuite_review",
        )


def _run_deai_gate(engine: Any, text: str) -> GateResult:
    try:
        return DeAIGate(engine).check(text, outward=True)
    except Exception as exc:  # noqa: BLE001 — fail closed
        return GateResult(
            verdict=HOLD,
            findings=[f"de-AI gate error (failing closed): {exc}"],
            gate="deai",
        )


def _run_fabrication_gate(engine: Any, text: str) -> GateResult:
    try:
        company_state = engine.get_company_state()
    except Exception:  # noqa: BLE001 — no state → no grounding → fail closed
        company_state = {}
    episodes = _gather_episodes(engine)
    try:
        return FabricationCheck(engine).check(
            text, company_state=company_state, episodes=episodes
        )
    except Exception as exc:  # noqa: BLE001 — fail closed
        return GateResult(
            verdict=HOLD,
            findings=[f"fabrication check error (failing closed): {exc}"],
            gate="fabrication",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DeliverableShim:
    """Minimal task stand-in for the C-suite reviewer when no task given."""

    def __init__(self, text: str):
        self.id = None
        self.title = "outward action"
        self.result = {"output": text}


def _gather_episodes(engine: Any) -> list[Any]:
    try:
        return engine.episodes.list(limit=_EPISODE_EVIDENCE_LIMIT)
    except Exception:  # noqa: BLE001 — episodes are best-effort evidence
        return []


def _resolve_class(value: Any) -> DeliverableClass:
    if isinstance(value, DeliverableClass):
        return value
    if isinstance(value, str):
        try:
            return DeliverableClass(value.strip().lower())
        except ValueError:
            pass
    # Conservative default: treat unknown outward work as published content
    # so the review gate fires (mirrors deliverables.py's bias).
    return DeliverableClass.PUBLISHED_CONTENT


def _attr(obj: Any, name: str, default: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


__all__ = ["PreflightResult", "run_preflight"]
