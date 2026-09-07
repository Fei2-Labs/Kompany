"""Default step executor for :class:`WorkflowRunner`.

Bridges declarative workflow YAML to the running engine: resolves the
declared ``agent_role`` via the agent registry, builds the prompt by
substituting prior step outputs into a template, calls the LLM through
``BaseAgent.call``, records cost / audit events, and respects the step's
``autonomy_tier`` for AutonomyGate routing.

MVP scope (1.0): agent-call and python_callable steps. Tool calls
declared on a step are recorded but not dispatched — that wires up
alongside the Tool registry in a follow-up slice. Subscribing to
AutonomyGate for ``approval`` / ``human_only`` tiers raises a
``StepNeedsApproval`` sentinel that callers handle (the WorkflowRunner
itself just halts; integration with the live approval thread happens in
the engine glue code).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from kompany.core.workflow_runner import StepResult

if TYPE_CHECKING:
    from kompany.agents.registry import AgentRegistry
    from kompany.core.workflow_runner import WorkflowRunner
    from kompany.plugins.contract import ToolContext

log = logging.getLogger(__name__)


class StepNeedsApproval(RuntimeError):
    """Raised when a step's autonomy_tier requires founder action.

    The :class:`WorkflowRunner` surfaces this as the step's ``error``
    field, halting the run. The caller routes the surfaced step through
    the live approval thread and re-resumes once approved.
    """


@dataclass
class ExecutorContext:
    """Carries the runtime services a step needs.

    Held intentionally narrow. New fields here = MINOR contract bump.
    """

    registry: "AgentRegistry"
    runner: "WorkflowRunner"
    company_state: dict[str, Any] | None = None
    directive_id: str | None = None
    initial_inputs: Mapping[str, Any] | None = None
    tool_context: "ToolContext | None" = None
    """Contract 1.1.0: the same service bundle Tools receive (documents,
    artifacts, approvals, journal, events, ledger, audit, settings).
    ``python_callable`` steps read it as ``ctx.tool_context``; None when the
    runner is driven without an engine (unit tests, dry runs)."""
    skills: Any = None
    """Contract 1.2.0: the company's ``SkillStore`` (or None). A step that
    declares ``skills:`` gets trigger-word-retrieved skills of the declared
    scopes prepended to its prompt (08-29 R3 selective injection)."""


def _format(template: str, scope: Mapping[str, Any]) -> str:
    """Lightweight ``str.format_map`` with missing-key tolerance.

    Workflows are authored by humans and prone to typos; rather than
    crashing on a missing variable, leave the placeholder verbatim so
    the LLM sees and (usually) recovers — and the audit trail captures
    the malformed prompt for debugging.
    """

    class _Tolerant(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    return template.format_map(_Tolerant(scope))


# Public name for callers outside this module (dry-run PREVIEW renders the
# same prompts the executor would send, via the same tolerant formatter).
render_prompt = _format


def default_step_executor(
    step: Mapping[str, Any],
    prior_outputs: Mapping[str, Any],
    ctx: ExecutorContext,
) -> StepResult:
    """Production executor wired by the engine when running workflows."""
    step_id = step["id"]
    role = step["agent_role"]
    autonomy = step.get("autonomy_tier", "auto")

    # Autonomy gating up front — refuse to execute non-auto steps. The
    # caller drives the live approval thread; we just signal.
    if autonomy == "human_only":
        # The engine still wants the prompt the LLM would have used so
        # it can surface that as the founder-facing suggestion. Build it
        # without calling the LLM.
        prompt = _format(step.get("prompt_template", ""), _scope(ctx, prior_outputs))
        return StepResult(
            step_id=step_id,
            output={"suggested_prompt": prompt, "kind": "human_only"},
            cost_usd=0.0,
        )

    if autonomy == "approval":
        # Surface as error; runner halts; engine glue routes to approval
        # thread and re-resumes with autonomy temporarily forced to
        # "auto" once approved. (Re-resume path is the engine's job, not
        # this module's.)
        return StepResult(
            step_id=step_id,
            error="needs_approval: step requires founder approval",
        )

    if autonomy != "auto":
        return StepResult(
            step_id=step_id,
            error=f"unknown_autonomy_tier: {autonomy!r}",
        )

    # python_callable escape hatch — opaque to cost preview by design.
    if step.get("python_callable"):
        fn = ctx.runner.resolve_python_callable(step_id)
        if fn is None:
            return StepResult(
                step_id=step_id,
                error=f"python_callable {step['python_callable']!r} not registered",
            )
        try:
            output = fn(prior_outputs, ctx)
        except Exception as exc:  # noqa: BLE001 — propagated via StepResult
            return StepResult(step_id=step_id, error=f"{type(exc).__name__}: {exc}")
        return StepResult(step_id=step_id, output=output, cost_usd=0.0)

    # Default = LLM-driven agent call.
    template = step.get("prompt_template", "")
    if not template:
        return StepResult(
            step_id=step_id,
            error="prompt_template missing (and no python_callable set)",
        )

    scope = _scope(ctx, prior_outputs)
    prompt = _format(template, scope)
    prompt = _inject_skills(step, role, prompt, ctx)

    try:
        agent = ctx.registry.get(role, company_state=ctx.company_state)
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            step_id=step_id,
            error=f"agent_resolution_failed: {type(exc).__name__}: {exc}",
        )

    try:
        response = agent.call(
            prompt=prompt,
            directive_id=ctx.directive_id,
            action_type=f"workflow.{ctx.runner.workflow_id}.{step_id}",
        )
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            step_id=step_id,
            error=f"llm_call_failed: {type(exc).__name__}: {exc}",
        )

    return StepResult(
        step_id=step_id,
        output=response.text,
        cost_usd=float(response.cost_usd),
    )


def skills_spec(step: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalise a step's ``skills:`` key. ``true`` → all scopes, limit 3;
    a mapping may set ``scopes`` (subset of builtin/company/agent), ``limit``
    and ``query`` (a template; defaults to the rendered prompt). Absent or
    ``false`` → no injection (reference workflows stay byte-identical)."""
    raw = step.get("skills")
    if not raw:
        return None
    spec: dict[str, Any] = {"scopes": None, "limit": 3, "query": None}
    if isinstance(raw, Mapping):
        if raw.get("scopes"):
            spec["scopes"] = [str(x) for x in raw["scopes"]]
        if raw.get("limit") is not None:
            spec["limit"] = int(raw["limit"])
        if raw.get("query"):
            spec["query"] = str(raw["query"])
    return spec


def _inject_skills(step: Mapping[str, Any], role: str, prompt: str, ctx: ExecutorContext) -> str:
    spec = skills_spec(step)
    store = getattr(ctx, "skills", None)
    if spec is None or store is None:
        return prompt
    query = _format(spec["query"], _scope(ctx, {})) if spec["query"] else prompt
    try:
        block = store.retrieve_text(role, query, limit=spec["limit"], scopes=spec["scopes"])
    except Exception as exc:  # noqa: BLE001 — skill retrieval never blocks a step
        log.warning("skill injection skipped for step %s: %s", step.get("id"), exc)
        return prompt
    return f"{block}\n\n---\n\n{prompt}" if block else prompt


def _scope(ctx: ExecutorContext, prior_outputs: Mapping[str, Any]) -> dict[str, Any]:
    scope: dict[str, Any] = {}
    if ctx.initial_inputs:
        scope.update(ctx.initial_inputs)
    scope.update(prior_outputs)
    return scope
