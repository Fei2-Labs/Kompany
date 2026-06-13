"""Shared constants for the episodes materializer."""

# Audit event types that are deliberately *included* in episode payloads.
# Everything else (chatty per-step events, internal traces) is filtered out
# to keep payloads under the "10–50 KB" target documented in the PRD.
_KEY_AUDIT_EVENT_TYPES: frozenset[str] = frozenset({
    "project.created",
    "project.completed",
    "project.failed",
    "directive.classified",
    "directive.routed",
    "directive.completed",
    "directive.failed",
    "governed_execution.released",
    "governed_execution.delivered",
    "learning.retrospective_completed",
    "learning.episode_recorded",
    "learning.episode_trimmed",
    "approval.approved",
    "approval.rejected",
    "debate.recorded",
})
