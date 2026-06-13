"""Project-plan decomposition helpers — extracted from runner.py per ADR-0003.

``decompose`` and ``decompose_unfiltered`` are pure functions that take
``engine`` + ``project`` and return a list of ``TaskSpec``.  They mirror the
logic that previously lived in ``ProjectRunner._decompose`` /
``_decompose_unfiltered`` without binding it to the class — keeping the class
body under the 500-line cap while leaving all call sites unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kompany.state.models import Project

from kompany.core.runner_parts._models import TaskDecomposition, TaskSpec


def decompose(engine, project: Project) -> list[TaskSpec]:
    """Decompose then apply the founder's hard-rule post-filter (#6).

    Deterministic: any TaskSpec whose title/prompt matches an
    ``exclude_capability`` founder rule is dropped before a task row
    is ever created — regardless of whether the CEO LLM honored the
    NEVER clause in its prompt. Drops are audited (best-effort).
    """
    from kompany.core import founder_config

    rules = getattr(engine, "get_founder_rules", lambda: None)()
    specs = decompose_unfiltered(engine, project, rules)
    kept, dropped = founder_config.filter_task_specs(specs, rules)
    if dropped:
        try:
            engine.audit.record(
                "founder_rules.tasks_filtered",
                f"Founder hard rules dropped {len(dropped)} task(s)",
                detail={
                    "dropped_titles": [d.title for d in dropped],
                    "excluded": founder_config.excluded_capabilities(rules),
                },
                project_id=project.id,
            )
        except Exception:  # noqa: BLE001 — best-effort
            pass
    return kept


def decompose_unfiltered(
    engine, project: Project, rules: dict | None = None
) -> list[TaskSpec]:
    """Use CEO to decompose a project's revenue paths into tasks.

    Two project shapes flow through here:

    1. **Revenue projects** (template ``apply_template`` revenue
       drafts): plan blob has ``paths`` → CEO LLM-decomposes into
       3-5 TaskSpecs.
    2. **First-move directives** (team-proposed at step 5 of
       onboarding): plan blob has ``week_plan`` (a 5-entry list,
       one per weekday) + ``proposer_role`` + ``other_agents_involved``.
       The team already decomposed the work; we just turn each day
       into a TaskSpec and rotate ownership through the proposer +
       collaborators. No extra LLM call — week_plan IS the plan.
    """
    # Branch 2: first-move directive — week_plan already exists.
    week_plan = project.plan.get("week_plan") or []
    if week_plan:
        proposer = (project.plan.get("proposer_role") or "ceo").lower()
        collabs = [
            str(r).lower()
            for r in (project.plan.get("other_agents_involved") or [])
        ]
        owners = [proposer, *collabs] or ["ceo"]
        tasks: list[TaskSpec] = []
        for i, line in enumerate(week_plan):
            title = str(line).strip()
            if not title:
                continue
            # Round-robin across owners so different agents take
            # different days — keeps the office visibly busy
            # instead of one CEO doing everything.
            owner = owners[i % len(owners)]
            tasks.append(
                TaskSpec(
                    title=title[:140],
                    assigned_agent=owner,
                    prompt=(
                        f"Project: {project.name}\n"
                        f"Day {i + 1} of week-1 plan: {title}\n\n"
                        "Execute this concretely. Return what you "
                        "did, what you found, and the next step."
                    ),
                )
            )
        return tasks

    # Branch 1: classic revenue path decomposition.
    paths = project.plan.get("paths", [])
    if not paths:
        return []

    # Pick the recommended path, or first available
    recommended = project.plan.get("recommended_path", "")
    target_path = None
    for p in paths:
        if p.get("name") == recommended:
            target_path = p
            break
    if not target_path:
        target_path = paths[0]

    state = engine.get_company_state()
    ceo = engine.registry.get("ceo", company_state=state)

    prompt = (
        f"Project: {project.name}\n"
        f"Revenue path: {target_path.get('name', 'unknown')}\n"
        f"Description: {target_path.get('description', '')}\n"
        f"Target revenue: €{target_path.get('estimated_revenue_eur', 0):.0f}\n"
        f"Timeframe: {target_path.get('timeframe', 'unknown')}\n\n"
        f"Break this into 3-5 concrete tasks. Available agents:\n"
        f"- researcher: market research, competitor analysis\n"
        f"- writer: content creation, proposals, landing pages\n"
        f"- analyst: financial modeling, ROI calculations\n"
        f"- builder: code, configurations, integrations\n"
        f"- procurement: sourcing, vendor evaluation\n\n"
        f"For each task also assign budget_cap_usd (AI spend cap for "
        f"that task, within this project's budget envelope: 0.50 for "
        f"a small/routine task, up to 5.00 only for genuinely complex "
        f"work) and max_turns (agentic loop turns, default 30).\n"
    )
    # Founder hard rules (#6): excluded capabilities go into the
    # prompt so no tokens are wasted planning them; the deterministic
    # post-filter in decompose backstops the LLM regardless.
    from kompany.core.founder_config import never_propose_clause

    prompt += never_propose_clause(rules)

    resp = ceo.call_structured(
        prompt=prompt,
        output_schema=TaskDecomposition,
        action_type="agent_task_execute",
    )
    return resp.parsed.tasks
