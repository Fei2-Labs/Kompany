"""Self-update pipeline — governed code self-modification (06-12 PRD 3).

The running instance never edits itself in place: self-originated code
changes flow clone → harness session → tier check → tests → founder
approval card. See ``CONSTITUTION.md`` "Source code self-modification".
"""

from __future__ import annotations

from kompany.core.self_update.pipeline import propose_self_update
from kompany.core.self_update.tiers import T3_PROMPT_NOTE, classify_paths
from kompany.core.self_update.workspace import (
    commit_all,
    diff_stats,
    discard_branch,
    ensure_clone,
    start_branch,
)

__all__ = [
    "T3_PROMPT_NOTE",
    "classify_paths",
    "commit_all",
    "diff_stats",
    "discard_branch",
    "ensure_clone",
    "propose_self_update",
    "start_branch",
]
