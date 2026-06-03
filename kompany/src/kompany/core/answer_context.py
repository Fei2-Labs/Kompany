"""Helpers for composing bounded CEO answer context."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kompany.core.engine import KompanyEngine


MAX_ANSWER_PROJECTS = 5
MAX_ANSWER_TASKS = 8
MAX_ANSWER_STAFF = 12
MAX_RECENT_COMPLETED = 3
TARGETS_EDIT_PATH = "/ui/onboarding.html"


def _status_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _has_target_signal(engine: KompanyEngine) -> bool:
    targets = engine.get_targets()
    return (
        targets.initial_budget > 0
        or targets.revenue_target > 0
        or targets.customer_target is not None
        or bool(targets.deadline)
    )


def _targets_status(engine: KompanyEngine) -> str:
    bundle = engine.get_targets_bundle()
    if bundle.agreed is not None:
        return "set (agreed)"
    if _has_target_signal(engine):
        return "set (founder)"
    return "missing"


def _compose_targets_section(
    engine: KompanyEngine,
    targets_summary: str,
) -> str:
    status = _targets_status(engine)
    lines = [
        "MISSION / TARGETS CURRENTLY SET:",
        f"  Status: {status}",
        f"  Summary: {targets_summary}",
    ]
    if status.startswith("set"):
        lines.append(
            "  Change/re-specify path: "
            f"{TARGETS_EDIT_PATH} (current product uses onboarding; "
            "settings does not edit company targets yet)"
        )
    else:
        lines.append(
            "  Set/re-specify path: "
            f"{TARGETS_EDIT_PATH} (current product uses onboarding; "
            "settings does not edit company targets yet)"
        )
    return "\n".join(lines)


def _compose_active_work_section(engine: KompanyEngine) -> str:
    active_projects = engine.projects.list_active()
    lines = [
        "ACTIVE WORK NOW:",
        f"  Active projects: {len(active_projects)}",
    ]

    if not active_projects:
        lines.extend([
            "  Open tasks in active projects: 0",
            "  (none right now)",
        ])
        return "\n".join(lines)

    visible_projects = active_projects[:MAX_ANSWER_PROJECTS]
    open_tasks_by_project: dict[str, list] = {}
    open_task_count = 0
    for project in visible_projects:
        try:
            tasks = engine.projects.list_tasks(project.id)
        except Exception:  # pragma: no cover
            tasks = []
        open_tasks = [
            task
            for task in tasks
            if _status_value(task.status) not in {"completed", "delivered", "failed"}
        ]
        open_tasks_by_project[project.id] = open_tasks
        open_task_count += len(open_tasks)

    lines.insert(2, f"  Open tasks in active projects: {open_task_count}")
    for project in visible_projects:
        lines.append(
            f"  - {project.name} "
            f"(€{project.funded_amount:.2f}/€{project.target_amount or 0:.2f})"
        )
        open_tasks = open_tasks_by_project.get(project.id, [])
        if not open_tasks:
            lines.append("      • no open tasks listed")
            continue
        for task in open_tasks[:MAX_ANSWER_TASKS]:
            lines.append(f"      • {task.title} [{_status_value(task.status)}]")
        if len(open_tasks) > MAX_ANSWER_TASKS:
            lines.append(
                f"      • …{len(open_tasks) - MAX_ANSWER_TASKS} more open task(s) not shown"
            )
    if len(active_projects) > MAX_ANSWER_PROJECTS:
        lines.append(
            f"  …{len(active_projects) - MAX_ANSWER_PROJECTS} more active project(s) not shown"
        )
    return "\n".join(lines)


def _compose_recent_completed_section(engine: KompanyEngine) -> str:
    try:
        episodes = engine.episodes.list(limit=MAX_RECENT_COMPLETED + 1)
    except Exception:  # pragma: no cover
        return "RECENT COMPLETED WORK:\n  (unavailable)"

    if not episodes:
        return "RECENT COMPLETED WORK:\n  (none yet)"

    visible_episodes = episodes[:MAX_RECENT_COMPLETED]
    lines = [
        "RECENT COMPLETED WORK:",
        f"  Completed episodes/projects shown: {len(visible_episodes)}",
    ]
    for episode in visible_episodes:
        summary = (episode.get("summary") or "").strip()
        if not summary:
            project_id = episode.get("project_id")
            project = engine.projects.get(project_id) if project_id else None
            if project is not None:
                summary = f"{project.name} ({_status_value(project.status)})"
            else:
                summary = "completed project"
        lines.append(f"  - {summary}")
    if len(episodes) > MAX_RECENT_COMPLETED:
        lines.append("  …more completed project(s) not shown")
    return "\n".join(lines)


def compose_answer_context(
    engine: KompanyEngine,
    *,
    targets_summary: str,
) -> tuple[str, bool]:
    """Build a bounded real-state snapshot for the CEO ``answer`` route.

    The answer route needs enough real company state to answer founder
    questions directly, but must stay cheap and bounded. The snapshot includes:

    * MISSION / TARGETS — authoritative targets summary + real edit path
    * FINANCIALS — via ``cfo.get_summary()``
    * ACTIVE WORK NOW — top active projects + capped open tasks
    * RECENT COMPLETED WORK — bounded recent episodes/projects
    * STAFF ACTIVITY — capped agent-status rows

    Returns ``(context_text, used_cfo)`` so the engine can report
    ``agents_used`` truthfully.
    """
    sections: list[str] = [f"Company: {engine.settings.company_name}"]
    used_cfo = False

    sections.append(_compose_targets_section(engine, targets_summary))

    try:
        summary = engine.registry.get("cfo").get_summary()
        used_cfo = True
        sections.append(
            "FINANCIALS:\n"
            f"  Balance: €{summary['balance']:.2f}\n"
            f"  Total income: €{summary['total_income']:.2f}\n"
            f"  Total expenses: €{summary['total_expenses']:.2f}\n"
            f"  Total AI costs: ${abs(summary['total_ai_costs']):.4f}"
        )
    except Exception:  # pragma: no cover — never let one store kill the answer
        sections.append("FINANCIALS:\n  (unavailable)")

    sections.append(_compose_active_work_section(engine))
    sections.append(_compose_recent_completed_section(engine))

    try:
        rows = engine.agent_status.list_all()
        if not rows:
            sections.append("STAFF ACTIVITY:\n  (no agents active)")
        else:
            lines = ["STAFF ACTIVITY:"]
            for row in rows[:MAX_ANSWER_STAFF]:
                role = row.get("agent_role", "?")
                state = row.get("status", "?")
                activity = (row.get("current_task") or "").strip()
                suffix = f" — {activity}" if activity else ""
                lines.append(f"  - {role}: {state}{suffix}")
            if len(rows) > MAX_ANSWER_STAFF:
                lines.append(
                    f"  …{len(rows) - MAX_ANSWER_STAFF} more staff row(s) not shown"
                )
            sections.append("\n".join(lines))
    except Exception:  # pragma: no cover
        sections.append("STAFF ACTIVITY:\n  (unavailable)")

    return "\n\n".join(sections), used_cfo
