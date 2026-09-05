"""Workflow YAML interpreter — the runtime side of ``Workflow`` plugins.

A Pro / community Workflow plugin (see :mod:`kompany.plugins.contract`)
declares its top-level orchestration as a YAML step list so the engine
can compute cost preview, AutonomyGate routing, and audit metadata
BEFORE running. Individual steps that need imperative logic point at a
Python callable via the workflow's ``python_callables`` mapping.

MVP scope: parsing, validation, cost summation, ordered step iteration.
Real LLM / Tool invocation is delegated to a pluggable ``StepExecutor``
so this module can ship in isolation; the production executor lands in
the Pro reference-workflows slice (A2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from kompany.plugins.contract import AutonomyTier, CostEstimate


class WorkflowYAMLInvalid(ValueError):
    """Raised when a workflow YAML is malformed or fails contract validation."""


_TOP_REQUIRED = ("workflow_id", "display_name", "steps")
_STEP_REQUIRED = ("id", "agent_role")
_VALID_AUTONOMY = {t.value for t in AutonomyTier}


@dataclass
class StepResult:
    step_id: str
    output: Any = None
    cost_usd: float = 0.0
    skipped: bool = False
    error: str | None = None


@dataclass
class WorkflowRunResult:
    workflow_id: str
    steps: list[StepResult] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    """All step outputs so far (seeded + produced) — the resume checkpoint."""

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps)

    @property
    def ok(self) -> bool:
        return all(s.error is None for s in self.steps)


# A StepExecutor takes the step dict + accumulated prior outputs + a free-form
# context object, and returns a StepResult. The engine wires its production
# executor (LLM + Tool dispatch + AutonomyGate) when constructing a runner.
StepExecutor = Callable[
    [Mapping[str, Any], Mapping[str, Any], Any],
    StepResult,
]


class WorkflowRunner:
    """Loads a workflow YAML and runs its steps in declared order."""

    def __init__(
        self,
        yaml_source: Path | str | dict[str, Any],
        python_callables: Mapping[str, Callable[..., Any]] | None = None,
        step_executor: StepExecutor | None = None,
    ):
        if isinstance(yaml_source, dict):
            self._data = dict(yaml_source)
        else:
            p = Path(yaml_source)
            if not p.exists():
                raise WorkflowYAMLInvalid(f"Workflow YAML not found: {p}")
            self._data = yaml.safe_load(p.read_text()) or {}
            if not isinstance(self._data, dict):
                raise WorkflowYAMLInvalid(
                    f"Workflow YAML must be a mapping: {p}"
                )

        self._validate(self._data)
        self.python_callables = dict(python_callables or {})
        self._step_executor = step_executor

    @staticmethod
    def _validate(data: dict[str, Any]) -> None:
        for field_ in _TOP_REQUIRED:
            if field_ not in data or data[field_] in (None, ""):
                raise WorkflowYAMLInvalid(
                    f"Workflow YAML missing required field '{field_}'"
                )
        steps = data["steps"]
        if not isinstance(steps, list) or len(steps) == 0:
            raise WorkflowYAMLInvalid("Workflow 'steps' must be a non-empty list")

        seen_ids: set[str] = set()
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise WorkflowYAMLInvalid(f"Step {i} must be a mapping")
            for field_ in _STEP_REQUIRED:
                if not step.get(field_):
                    raise WorkflowYAMLInvalid(
                        f"Step {i} missing required field '{field_}'"
                    )
            sid = step["id"]
            if sid in seen_ids:
                raise WorkflowYAMLInvalid(f"Duplicate step id: {sid!r}")
            seen_ids.add(sid)
            autonomy = step.get("autonomy_tier", "auto")
            if autonomy not in _VALID_AUTONOMY:
                raise WorkflowYAMLInvalid(
                    f"Step {sid!r} has invalid autonomy_tier '{autonomy}'. "
                    f"Valid: {sorted(_VALID_AUTONOMY)}"
                )

    @property
    def workflow_id(self) -> str:
        return self._data["workflow_id"]

    @property
    def display_name(self) -> str:
        return self._data["display_name"]

    @property
    def steps(self) -> list[dict[str, Any]]:
        return list(self._data["steps"])

    def estimate_cost(self) -> CostEstimate:
        """Sum of per-step ``cost_estimate_usd`` (LLM-side).

        Steps lacking a cost estimate are assumed free; ``python_callable``
        steps are opaque to the runner so their cost field is honored if
        present but otherwise treated as zero. Confidence drops below 1.0
        when any step lacks an estimate.
        """
        llm_total = 0.0
        missing = 0
        for step in self._data["steps"]:
            est = step.get("cost_estimate_usd")
            if est is None:
                missing += 1
                continue
            try:
                llm_total += float(est)
            except (TypeError, ValueError):
                missing += 1
        steps_count = len(self._data["steps"])
        confidence = 1.0 if missing == 0 else max(0.0, 1.0 - missing / steps_count)
        return CostEstimate(llm_usd=llm_total, confidence=confidence)

    def run(
        self,
        ctx: Any = None,
        *,
        start_at: str | None = None,
        prior_outputs: Mapping[str, Any] | None = None,
        force_auto: frozenset[str] | set[str] | None = None,
    ) -> WorkflowRunResult:
        """Execute steps in declared order.

        Requires a ``step_executor`` to have been provided to the
        constructor — this module ships without a default executor by
        design (see module docstring).

        Resume support (approval gates): ``start_at`` skips every step before
        the named one, ``prior_outputs`` seeds the outputs those skipped steps
        produced, and ``force_auto`` names steps whose ``autonomy_tier`` is
        treated as ``auto`` for this run only — the engine passes the step
        the founder just approved.
        """
        if self._step_executor is None:
            raise WorkflowYAMLInvalid(
                "WorkflowRunner has no step_executor configured. "
                "Pass one to the constructor or use a higher-level runner "
                "that wires the production executor."
            )
        if start_at is not None and start_at not in {s["id"] for s in self._data["steps"]}:
            raise WorkflowYAMLInvalid(f"start_at step {start_at!r} is not in this workflow")

        result = WorkflowRunResult(workflow_id=self.workflow_id)
        outputs: dict[str, Any] = dict(prior_outputs or {})
        forced = set(force_auto or ())
        started = start_at is None

        for step in self._data["steps"]:
            if not started:
                if step["id"] != start_at:
                    continue
                started = True
            if step["id"] in forced and step.get("autonomy_tier") != "auto":
                step = {**step, "autonomy_tier": "auto"}
            try:
                step_res = self._step_executor(step, outputs, ctx)
            except Exception as exc:  # noqa: BLE001 — surface via StepResult.error
                step_res = StepResult(
                    step_id=step["id"], error=f"{type(exc).__name__}: {exc}"
                )
            result.steps.append(step_res)
            if step_res.error:
                break
            outputs[step_res.step_id] = step_res.output

        result.outputs = outputs
        return result

    def resolve_python_callable(self, step_id: str) -> Callable[..., Any] | None:
        """Look up a Python escape-hatch callable for a step.

        Production step executors call this when they encounter a step
        with a ``python_callable`` field. Returns ``None`` if no mapping
        exists; the executor should raise to surface the misconfiguration.
        """
        for step in self._data["steps"]:
            if step["id"] != step_id:
                continue
            key = step.get("python_callable")
            if not key:
                return None
            return self.python_callables.get(key)
        return None
