"""Helpers for composing bounded CEO answer context."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kompany.core.engine import KompanyEngine


MAX_ANSWER_PROJECTS = 5
MAX_ANSWER_TASKS = 8
MAX_ANSWER_STAFF = 12


def compose_answer_context(engine: KompanyEngine) -> tuple[str, bool]:
    """Build a bounded real-state snapshot for the CEO ``answer`` route.

    The answer route needs enough real company state to answer founder
    questions directly, but must stay cheap and bounded. The snapshot includes:

    * FINANCIALS — via ``cfo.get_summary()``
    * ACTIVE PROJECTS — top projects + capped tasks per project
    * STAFF ACTIVITY — capped agent-status rows

    Returns ``(context_text, used_cfo)`` so the engine can report
    ``agents_used`` truthfully.
    """
    sections: list[str] = [f"Company: {engine.settings.company_name}"]
    used_cfo = False

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

    try:
        active = engine.projects.list_active()
        if not active:
            sections.append("ACTIVE PROJECTS:\n  (none)")
        else:
            lines = ["ACTIVE PROJECTS:"]
            for project in active[:MAX_ANSWER_PROJECTS]:
                lines.append(
                    f"  - {project.name} "
                    f"(€{project.funded_amount:.2f}/€{project.target_amount or 0:.2f})"
                )
                try:
                    tasks = engine.projects.list_tasks(project.id)
                except Exception:  # pragma: no cover
                    tasks = []
                for task in tasks[:MAX_ANSWER_TASKS]:
                    status = (
                        task.status.value
                        if hasattr(task.status, "value")
                        else str(task.status)
                    )
                    lines.append(f"      • {task.title} [{status}]")
                if len(tasks) > MAX_ANSWER_TASKS:
                    lines.append(
                        f"      • …{len(tasks) - MAX_ANSWER_TASKS} more task(s) not shown"
                    )
            if len(active) > MAX_ANSWER_PROJECTS:
                lines.append(
                    f"  …{len(active) - MAX_ANSWER_PROJECTS} more active project(s) not shown"
                )
            sections.append("\n".join(lines))
    except Exception:  # pragma: no cover
        sections.append("ACTIVE PROJECTS:\n  (unavailable)")

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
