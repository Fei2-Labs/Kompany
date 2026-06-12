"""Evidence-based outcome classification for harness runs (PRD D6).

Split out of ``executor.py`` (≤500-line rule): the executor runs the
session and books the results; this module decides what the run MEANT —
completed / delivered / blocked / hard failure.
"""

from __future__ import annotations

from kompany.core.harness import HarnessResult

# Exit statuses that mean the vehicle itself broke (CLI died, wall-clock
# timeout) rather than the work being capped or finished.
_HARD_FAILURE_EXITS = frozenset({"error", "timeout"})


def is_hard_failure(result: HarnessResult) -> bool:
    """Hard error/timeout with no work evidence → honest ``FAILED``.

    The legacy path mapped every adapter error into ``delivered``, telling
    the founder "review the output" when there was none. A run that ends
    ``error``/``timeout`` without a workspace diff produced nothing — mark
    it FAILED (retryable: the session id is persisted, a re-run resumes).
    ``budget_exceeded`` and ``error_max_turns`` are NOT failures — they
    keep their pause/delivered semantics (work happened, caps fired).
    With diff evidence despite the error, real work happened → the normal
    classifier takes it (``delivered``).
    """
    if result.exit_status not in _HARD_FAILURE_EXITS:
        return False
    if result.files_changed:
        return False
    return True


def classify_harness_outcome(
    result: HarnessResult, tool_events: int
) -> tuple[str, str]:
    """Evidence-based outcome (PRD D6) — replaces phrase matching.

    ``completed`` only with evidence: a workspace diff, or (exit success
    AND zero permission denials AND real tool activity AND a final
    answer). Denials → ``blocked`` with the denied tools listed as the
    founder action (connect/approve, never "do it yourself"). Everything
    else is ``delivered`` — an asset the founder must act on.
    """
    if result.permission_denials:
        tools = sorted(
            {str(d.get("tool_name", "?")) for d in result.permission_denials}
        )
        # Accurate now that permission routing lands the request in the
        # inbox (PR5): approving there and re-running the task makes the
        # session re-ask, and an approved request resolves instantly.
        action = (
            f"Approve the pending permission request(s) for: "
            f"{', '.join(tools)} in the inbox (or connect the required "
            "account) — the task resumes on its next run and the session "
            "re-asks automatically."
        )
        return "blocked", action
    if result.exit_status == "success":
        if result.files_changed:
            return (
                "completed",
                "Review the files produced in the project workspace.",
            )
        if tool_events > 0 and result.final_text.strip():
            return (
                "completed",
                "Review the session's work in the project workspace.",
            )
    if result.exit_status == "error_max_turns":
        # Real work happened before the cap kill (asset state, not a
        # failure) — but the resume affordance must not be lost: the
        # session id is persisted on the task for continuation.
        return (
            "delivered",
            "The session hit its turn cap with partial work in the "
            "workspace — review it; the session is saved and re-running "
            "the task continues from where it stopped.",
        )
    return "delivered", "Review the output above and act on it."


__all__ = ["classify_harness_outcome", "is_hard_failure"]
