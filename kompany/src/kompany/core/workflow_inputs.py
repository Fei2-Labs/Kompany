"""Workflow input resolution — explicit > ``source`` auto-fill > ``default``.

Companion to :mod:`kompany.core.engine_parts.workflows`. Kept separate so
the engine mixin stays under the file-size cap (ADR-0003) and so the
resolution table is testable without an engine.

Resolution runs BEFORE ``run_scope`` / audit / any LLM call: a workflow
whose required inputs are missing raises :class:`WorkflowInputsMissing`
and spends nothing. The same resolver feeds the dry-run PREVIEW so the
founder sees exactly the prompts that would be sent.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Mapping

from kompany.core.step_executor import render_prompt

if TYPE_CHECKING:
    from kompany.core.workflow_runner import WorkflowRunner


class WorkflowInputsMissing(ValueError):
    """Raised before any spend when a workflow's required inputs are absent.

    ``missing`` carries the normalised input specs (name / description /
    example) and ``example_payload`` a ready-to-paste ``--json-inputs``
    object built from the declared ``example`` values.
    """

    def __init__(self, workflow_id: str, missing: list[dict[str, Any]]):
        self.workflow_id = workflow_id
        self.missing = missing
        self.example_payload = {
            spec["name"]: spec.get("example") if spec.get("example") is not None else "..."
            for spec in missing
        }
        lines = [
            f"Workflow {workflow_id!r} is missing required input(s); nothing was run "
            f"and no cost was booked:"
        ]
        for spec in missing:
            desc = spec.get("description") or "(no description)"
            lines.append(f"  - {spec['name']}: {desc}")
        lines.append(
            "Pass them with --json-inputs, e.g. "
            f"--json-inputs '{json.dumps(self.example_payload, ensure_ascii=False)}'"
        )
        super().__init__("\n".join(lines))

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "workflow_inputs_missing",
            "workflow_id": self.workflow_id,
            "missing": self.missing,
            "example_inputs": self.example_payload,
            "message": str(self),
        }


# ---------------------------------------------------------------------------
# ``source:`` auto-fill
# ---------------------------------------------------------------------------


def _targets(engine: Any) -> Any | None:
    getter = getattr(engine, "get_targets", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 — targets are optional context
        return None


def _company_state(engine: Any) -> dict[str, Any]:
    getter = getattr(engine, "_workflow_company_state", None)
    if not callable(getter):
        return {}
    try:
        return getter() or {}
    except Exception:  # noqa: BLE001
        return {}


def _ledger_call(engine: Any, method: str, **kwargs: Any) -> Any | None:
    ledger = getattr(engine, "ledger", None)
    fn = getattr(ledger, method, None)
    if not callable(fn):
        return None
    try:
        return fn(**kwargs)
    except Exception:  # noqa: BLE001 — a ledger read must never block a run
        return None


def _target_attr(engine: Any, attr: str) -> Any | None:
    targets = _targets(engine)
    return getattr(targets, attr, None) if targets is not None else None


# ``source`` string → resolver. ``None`` from a resolver = unavailable →
# the input falls back to ``default`` or counts as missing.
_SOURCES: dict[str, Callable[[Any], Any | None]] = {
    "company.budget_remaining_usd": lambda e: _ledger_call(e, "get_balance"),
    "company.spend_last_7d_usd": lambda e: _ledger_call(e, "spent_in_window", days=7),
    "company.revenue_last_7d_usd": lambda e: _ledger_call(e, "revenue_in_window", days=7),
    # Customer acquisition is not tracked by the ledger yet. ``0`` is an
    # honest placeholder — the prompts that consume it say "not tracked".
    "company.new_customers_last_7d": lambda e: 0,
    "company.revenue_target_usd": lambda e: _target_attr(e, "revenue_target"),
    "company.customer_target": lambda e: _target_attr(e, "customer_target"),
    "company.deadline": lambda e: _target_attr(e, "deadline"),
    "company.name": lambda e: _company_state(e).get("name"),
    "company.goal": lambda e: _company_state(e).get("goal"),
}

KNOWN_SOURCES: tuple[str, ...] = tuple(_SOURCES)


def resolve_source(engine: Any, source: str) -> Any | None:
    """Resolve one ``source:`` key against the engine; ``None`` = unavailable."""
    fn = _SOURCES.get(source)
    if fn is None:
        return None
    return fn(engine)


def resolve_inputs(
    engine: Any,
    runner: "WorkflowRunner",
    explicit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the initial input scope for a run.

    Precedence per declared input: explicit value > ``source`` auto-fill >
    ``default``. Undeclared explicit keys pass through untouched (plugin
    YAMLs without an ``inputs:`` block keep working). Raises
    :class:`WorkflowInputsMissing` when a ``required`` input is still
    ``None`` afterwards.
    """
    scope: dict[str, Any] = dict(explicit or {})
    for spec in runner.inputs:
        name = spec["name"]
        if scope.get(name) is not None:
            continue
        value: Any | None = None
        if spec.get("source"):
            value = resolve_source(engine, spec["source"])
        if value is None and spec.get("default") is not None:
            value = spec["default"]
        if value is not None:
            scope[name] = value
    missing_names = runner.missing_inputs(scope)
    if missing_names:
        specs = [s for s in runner.inputs if s["name"] in missing_names]
        raise WorkflowInputsMissing(runner.workflow_id, specs)
    return scope


# ---------------------------------------------------------------------------
# Dry run — PREVIEW without spend
# ---------------------------------------------------------------------------


def dry_run_envelope(
    runner: "WorkflowRunner",
    inputs: Mapping[str, Any],
    *,
    project_id: str | None,
) -> dict[str, Any]:
    """Same envelope shape as a real run, with rendered prompts and no cost.

    Prior-step placeholders stay verbatim (``{probe_demand}``) because no
    step has produced output; the founder sees where each output lands.
    """
    estimate = runner.estimate_cost()
    steps = [
        {
            "step_id": s["id"],
            "agent_role": s["agent_role"],
            "autonomy_tier": s.get("autonomy_tier", "auto"),
            "cost_estimate_usd": s.get("cost_estimate_usd"),
            "python_callable": s.get("python_callable"),
            "prompt": render_prompt(s.get("prompt_template") or "", inputs),
            "output": None,
            "cost_usd": 0.0,
            "error": None,
        }
        for s in runner.steps
    ]
    return {
        "workflow_id": runner.workflow_id,
        "run_id": None,
        "project_id": project_id,
        "ok": True,
        "status": "dry_run",
        "resumed_from": None,
        "inputs": dict(inputs),
        "estimated_cost_usd": estimate.total_usd,
        "estimate_confidence": estimate.confidence,
        "total_cost_usd": 0.0,
        "steps": steps,
    }


__all__ = [
    "KNOWN_SOURCES",
    "WorkflowInputsMissing",
    "dry_run_envelope",
    "resolve_inputs",
    "resolve_source",
]
