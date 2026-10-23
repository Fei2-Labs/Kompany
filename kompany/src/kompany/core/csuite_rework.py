"""C-suite HOLD goes back to the team before it reaches the founder.

A HOLD is a defect report on the team's work, so the first move is a revision
round, not a founder card. ``request_csuite_review`` calls
:func:`route_hold` before filing a card:

* Reviewer lines that only say the reviewer could not open files or run
  commands are dropped. Reviewers see a text excerpt, never the workspace, so
  "cannot access the filesystem" says nothing about the deliverable.
* An empty deliverable (no output and no files) is not reviewed at all — the
  run produced nothing, so the task is requeued as a failed attempt.
* A HOLD with real defects requeues the task with the defects as revision
  feedback, up to ``csuite_review_max_rounds`` rounds. Only after that does
  the founder get a card.
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_MAX_ROUNDS = 2

# Reviewer complaints about its own missing tools, in English and Chinese.
_TOOLING_PATTERNS = re.compile(
    r"(cannot|can't|could not|couldn't|unable to|no|without|not)\s+(access|open|read|run|execute|reach|inspect|verify)"
    r".{0,40}(file|filesystem|file system|terminal|shell|command|repo|workspace|test|disk)"
    r"|no (filesystem|file system|terminal|shell) access"
    r"|reviewer unavailable"
    r"|无法(访问|取得|运行|执行|读取|打开|验证|重建)"
    r"|未能(修改|运行|执行|重建|访问|读取|验证)"
    r"|(未|没有)提供(文件系统|终端)",
    re.IGNORECASE,
)


def is_tooling_defect(defect: str) -> bool:
    """True when the line is about the reviewer's access, not the work."""
    return bool(_TOOLING_PATTERNS.search(defect or ""))


def strip_tooling(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop tooling lines; a HOLD backed only by tooling lines becomes PASS.

    Failed reviewer calls (``error``) are kept untouched — those still fail
    closed in the caller.
    """
    cleaned: list[dict[str, Any]] = []
    for f in findings:
        if f.get("error"):
            cleaned.append(f)
            continue
        raw = [str(d) for d in f.get("defects") or []]
        real = [d for d in raw if not is_tooling_defect(d)]
        verdict = f.get("verdict")
        if verdict == "HOLD" and raw and not real:
            verdict = "PASS"
        out = {**f, "verdict": verdict, "defects": real}
        if len(real) != len(raw):
            out["tooling_defects_dropped"] = len(raw) - len(real)
        cleaned.append(out)
    return cleaned


def is_empty_deliverable(task: Any) -> bool:
    """No output text and no files: there is nothing to review."""
    result = getattr(task, "result", None) or {}
    if not isinstance(result, dict):
        return True
    from kompany.core.harness.workspace import is_runner_artifact

    files = [f for f in result.get("files_changed") or [] if not is_runner_artifact(str(f))]
    return not str(result.get("output") or "").strip() and not files


def feedback_text(findings: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for f in findings:
        for d in f.get("defects") or []:
            lines.append(f"- [{str(f.get('role') or 'reviewer').upper()}] {d}")
    return "\n".join(lines)


def _max_rounds(engine: Any) -> int:
    getter = getattr(engine, "_get_int_config", None)
    if callable(getter):
        try:
            return int(getter("csuite_review_max_rounds", default=DEFAULT_MAX_ROUNDS))
        except Exception:  # noqa: BLE001 — config read must not break the gate
            pass
    return DEFAULT_MAX_ROUNDS


def route_hold(engine: Any, task: Any, project: Any, findings: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Send a HOLD back to the team when rounds remain.

    Returns a summary when the task was requeued (no card), or ``None`` when
    the rounds are spent and the caller should file the founder card.
    """
    result = dict(getattr(task, "result", None) or {})
    rounds = int(result.get("csuite_rework_rounds") or 0)
    if rounds >= _max_rounds(engine):
        return None
    feedback = feedback_text(findings)
    result.update({
        "csuite_rework_rounds": rounds + 1,
        "csuite_revision_feedback": feedback,
        "csuite_review_pending": False,
    })
    from kompany.state.models import TaskStatus

    engine.projects.update_task_status(task.id, TaskStatus.PENDING, result=result)
    engine.projects.requeue_task(
        task.id, reason=f"C-suite HOLD, revision round {rounds + 1}", reset_retries=True
    )
    engine.audit.record(
        "csuite_review.rework",
        f"C-suite HOLD sent back to the team (round {rounds + 1})",
        detail={"task_id": task.id, "round": rounds + 1, "findings": findings},
        agent_role=getattr(task, "assigned_agent", None),
        project_id=getattr(project, "id", None),
    )
    return {"status": "rework", "round": rounds + 1, "findings": findings}


def requeue_empty(engine: Any, task: Any, project: Any) -> dict[str, Any]:
    """An empty deliverable is a failed attempt; retry it instead of reviewing."""
    from kompany.core.harness_execution.retry import requeue_or_block

    requeue_or_block(engine, task, "deliverable was empty: no output and no files")
    engine.audit.record(
        "csuite_review.skipped_empty",
        "Empty deliverable sent back to the team instead of review",
        detail={"task_id": task.id},
        agent_role=getattr(task, "assigned_agent", None),
        project_id=getattr(project, "id", None),
    )
    return {"status": "empty_requeued"}


__all__ = [
    "feedback_text",
    "is_empty_deliverable",
    "is_tooling_defect",
    "requeue_empty",
    "route_hold",
    "strip_tooling",
]
