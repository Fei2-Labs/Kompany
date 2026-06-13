"""Sentinel kind strings + public exception for the watchdog.

Re-exported by :mod:`kompany.core.watchdog` — do not import from here
directly.
"""

from __future__ import annotations

# Sentinel kinds the LLM wrapper writes. Re-exported for callers that
# want to avoid magic strings.
KIND_SILENT_RUN = "silent_run"
KIND_RECOVERED = "recovered"
KIND_RETRY_EXHAUSTED = "retry_exhausted"
KIND_STRANDED_IN_PROGRESS = "stranded_in_progress"
KIND_STRANDED_TODO = "stranded_todo"
KIND_RUNWAY_ALERT = "runway_alert"
KIND_GLOSSARY_DRIFT_ALERT = "glossary_drift_alert"


class LLMUnavailable(RuntimeError):
    """Raised by :class:`kompany.llm.client.LLMClient` when both the
    primary call and the single retry fail.

    The engine catches this to transition the owning task to
    ``stranded_in_progress`` and emit a ``retry_exhausted`` event.
    """


__all__ = [
    "LLMUnavailable",
    "KIND_GLOSSARY_DRIFT_ALERT",
    "KIND_RECOVERED",
    "KIND_RETRY_EXHAUSTED",
    "KIND_RUNWAY_ALERT",
    "KIND_SILENT_RUN",
    "KIND_STRANDED_IN_PROGRESS",
    "KIND_STRANDED_TODO",
]
