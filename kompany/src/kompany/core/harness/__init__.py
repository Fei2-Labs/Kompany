"""Harness execution leg — loop-vehicle adapters (egress).

A harness CLI here is Kompany's execution backend for agent tasks, derived
internally from the founder's ModelSource (never founder-facing). Distinct
from the ingress harness-compatibility rule for ``interfaces/`` — see the
scope note in ``.trellis/spec/guides/harness-compatibility.md``.
"""

from __future__ import annotations

from kompany.core.harness.budget import BudgetMeter
from kompany.core.harness.claude_code_runner import ClaudeCodeRunner
from kompany.core.harness.codex_runner import CodexRunner
from kompany.core.harness.native_runner import NativeRunner
from kompany.core.harness.opencode_runner import OpencodeRunner
from kompany.core.harness.protocol import (
    EventCallback,
    EventKind,
    HandoffBrief,
    HarnessCaps,
    HarnessEvent,
    HarnessResult,
    HarnessRunner,
)
from kompany.core.harness.runner_utils import DEFAULT_TIMEOUT_SECONDS, RunAbort
from kompany.core.harness.safety import assert_workspace_safe
from kompany.core.harness.workspace import ensure_workspace, git_files_changed

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "BudgetMeter",
    "ClaudeCodeRunner",
    "CodexRunner",
    "EventCallback",
    "EventKind",
    "HandoffBrief",
    "HarnessCaps",
    "HarnessEvent",
    "HarnessResult",
    "HarnessRunner",
    "NativeRunner",
    "OpencodeRunner",
    "RunAbort",
    "assert_workspace_safe",
    "ensure_workspace",
    "git_files_changed",
]
