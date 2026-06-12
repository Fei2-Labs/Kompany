"""Claude Code loop vehicle — agentic ``claude -p`` sessions, stream-json.

Extends the single-shot provider (``kompany/llm/claude_code.py``) from
"LLM call" to "agentic run": tools on, stream-json events, session resume,
native budget cap. Flags and event schema follow the verified research at
``.trellis/tasks/06-11-harness-execution-leg/research/claude-headless-interface.md``
(claude CLI 2.1.173).

Notes from research baked in here:

- ``--max-budget-usd`` is the preferred cap — killing the process forfeits
  the authoritative ``total_cost_usd``.
- Denied permissions arrive in the final ``result`` event with exit 0;
  they are surfaced in ``HarnessResult.permission_denials``, never swallowed.
- ``error_max_turns`` exits 1 but still reports full cost/usage.
- ``CLAUDE*`` env vars are scrubbed (nested-invocation leakage: a child
  inheriting ``CLAUDECODE=1`` etc. can silently get ``bypassPermissions``).
- No per-message cost exists mid-stream, so this vehicle emits ``turn``
  events with token usage instead of ``cost_delta`` events.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from kompany.core.harness.protocol import (
    EventCallback,
    HandoffBrief,
    HarnessCaps,
    HarnessResult,
)
from kompany.core.harness.runner_utils import (
    DEFAULT_TIMEOUT_SECONDS,
    build_handoff_brief,
    emit_event,
    run_streaming,
    scrubbed_env,
)
from kompany.core.harness.safety import assert_workspace_safe
from kompany.core.harness.workspace import git_files_changed

__all__ = ["DEFAULT_TIMEOUT_SECONDS", "ClaudeCodeRunner"]

# Per-process cache for `claude --help` probing (key: "safe_mode").
_HELP_CACHE: dict[str, bool] = {}

_MISSING_BINARY_HINT = (
    "claude CLI not found on PATH — install Claude Code or "
    "switch the ModelSource to an API-key provider"
)


def _probe_safe_mode() -> bool:
    """Check whether the installed CLI supports ``--safe-mode`` (v2.1.169+)."""
    try:
        proc = subprocess.run(
            ["claude", "--help"],
            capture_output=True,
            text=True,
            timeout=30.0,
            stdin=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return "--safe-mode" in f"{proc.stdout or ''}{proc.stderr or ''}"


def _supports_safe_mode() -> bool:
    if "safe_mode" not in _HELP_CACHE:
        _HELP_CACHE["safe_mode"] = _probe_safe_mode()
    return _HELP_CACHE["safe_mode"]


class ClaudeCodeRunner:
    """First loop vehicle: Claude subscription → claude CLI (PRD D1)."""

    def __init__(
        self,
        model: str = "sonnet",
        permission_mode: str = "default",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._model = model
        self._permission_mode = permission_mode
        self._timeout_seconds = timeout_seconds

    @property
    def vehicle_name(self) -> str:
        return "claude_code"

    def start(
        self,
        prompt: str,
        workspace: Path,
        caps: HarnessCaps,
        on_event: EventCallback | None = None,
    ) -> HarnessResult:
        return self._run(prompt, workspace, caps, on_event, resume_session_id=None)

    def resume(
        self,
        session_id: str,
        prompt: str,
        workspace: Path,
        caps: HarnessCaps,
        on_event: EventCallback | None = None,
    ) -> HarnessResult:
        return self._run(
            prompt, workspace, caps, on_event, resume_session_id=session_id
        )

    def handoff(self, result: HarnessResult) -> HandoffBrief:
        """Assemble a portability brief from result fields (non-LLM)."""
        return build_handoff_brief(result, self.vehicle_name)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _build_command(
        self,
        prompt: str,
        caps: HarnessCaps,
        resume_session_id: str | None,
    ) -> list[str]:
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            str(caps.max_turns),
            "--model",
            self._model,
            "--permission-mode",
            self._permission_mode,
        ]
        if caps.budget_cap_usd is not None:
            cmd += ["--max-budget-usd", str(caps.budget_cap_usd)]
        # --safe-mode disables ALL customizations including --mcp-config
        # servers (verified live on CLI 2.1.175: the D5 permission-gate MCP
        # reports "Available MCP tools: none" under --safe-mode and the run
        # dies mid-session with exit 1). When the executor injects an
        # --mcp-config, fall back to the weaker --setting-sources "" shield
        # so the gate server stays reachable; --strict-mcp-config in the
        # routing args still blocks every other MCP server.
        routing_mcp = bool(caps.extra_cli_args) and (
            "--mcp-config" in caps.extra_cli_args
        )
        if _supports_safe_mode() and not routing_mcp:
            cmd.append("--safe-mode")
        else:
            # Pre-2.1.169 fallback: partial shield only (research pitfall 1).
            cmd += ["--setting-sources", ""]
        # Executor-injected vehicle flags (PRD D5 permission routing).
        if caps.extra_cli_args:
            cmd += list(caps.extra_cli_args)
        if resume_session_id is not None:
            cmd += ["--resume", resume_session_id]
        return cmd

    def _run(
        self,
        prompt: str,
        workspace: Path,
        caps: HarnessCaps,
        on_event: EventCallback | None,
        resume_session_id: str | None,
    ) -> HarnessResult:
        assert_workspace_safe(workspace)

        session_id: str | None = None
        result_event: dict[str, Any] | None = None

        def handle(event: dict[str, Any]) -> bool:
            nonlocal session_id, result_event
            etype = event.get("type")
            if etype == "system" and event.get("subtype") == "init":
                session_id = event.get("session_id")
                emit_event(
                    on_event,
                    "session_started",
                    {
                        "session_id": session_id,
                        "model": event.get("model"),
                        "permission_mode": event.get("permissionMode"),
                    },
                    raw=event,
                )
            elif etype == "assistant":
                self._handle_assistant(event, on_event)
            elif etype == "result":
                result_event = event
                self._handle_result_event(event, on_event)
            return True

        outcome = run_streaming(
            cmd=self._build_command(prompt, caps, resume_session_id),
            workspace=workspace,
            env=scrubbed_env(),
            timeout_seconds=self._timeout_seconds,
            handle_event=handle,
            missing_binary_hint=_MISSING_BINARY_HINT,
        )

        return self._build_result(
            result_event=result_event,
            # A resume that dies before any event must not lose the known
            # session id — it stays resumable.
            session_id=session_id or resume_session_id,
            workspace=workspace,
            timed_out=outcome.timed_out,
            returncode=outcome.returncode,
            stderr_tail=outcome.stderr_tail,
            on_event=on_event,
        )

    @staticmethod
    def _handle_assistant(
        event: dict[str, Any], on_event: EventCallback | None
    ) -> None:
        message = event.get("message") or {}
        usage = message.get("usage") or {}
        emit_event(on_event, "turn", {"usage": usage}, raw=event)
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                emit_event(on_event, "text", {"text": block.get("text", "")})
            elif btype == "tool_use":
                emit_event(
                    on_event,
                    "tool_use",
                    {
                        "tool_name": block.get("name", ""),
                        "tool_input": block.get("input") or {},
                        "tool_use_id": block.get("id"),
                    },
                )

    @staticmethod
    def _handle_result_event(
        event: dict[str, Any], on_event: EventCallback | None
    ) -> None:
        # Exit 0 ≠ nothing was blocked — surface every denial (pitfall 3).
        for denial in event.get("permission_denials") or []:
            if isinstance(denial, dict):
                emit_event(on_event, "permission_denied", dict(denial))
        summary = {
            "subtype": event.get("subtype"),
            "total_cost_usd": event.get("total_cost_usd"),
            "num_turns": event.get("num_turns"),
        }
        if event.get("is_error"):
            summary["errors"] = event.get("errors") or []
            emit_event(on_event, "failed", summary, raw=event)
        else:
            emit_event(on_event, "completed", summary, raw=event)

    def _build_result(
        self,
        result_event: dict[str, Any] | None,
        session_id: str | None,
        workspace: Path,
        timed_out: bool,
        returncode: int | None,
        stderr_tail: str,
        on_event: EventCallback | None,
    ) -> HarnessResult:
        files_changed = git_files_changed(Path(workspace))
        if result_event is not None:
            # Authoritative path — cost/usage come from the result event,
            # even on error subtypes (error_max_turns still reports cost).
            usage = result_event.get("usage") or {}
            subtype = str(result_event.get("subtype") or "success")
            is_error = bool(result_event.get("is_error"))
            errors = result_event.get("errors") or []
            cost = result_event.get("total_cost_usd")
            return HarnessResult(
                final_text=str(result_event.get("result") or ""),
                session_id=session_id or result_event.get("session_id"),
                files_changed=files_changed,
                cost_usd=float(cost) if cost is not None else None,
                tokens_in=int(usage.get("input_tokens") or 0),
                tokens_out=int(usage.get("output_tokens") or 0),
                permission_denials=[
                    dict(d)
                    for d in result_event.get("permission_denials") or []
                    if isinstance(d, dict)
                ],
                exit_status=subtype if is_error else "success",
                error="; ".join(str(e) for e in errors) if is_error else None,
            )
        if timed_out:
            error = (
                f"claude CLI run exceeded the {self._timeout_seconds:.0f}s "
                "wall-clock timeout and was terminated; no result event — "
                "cost is unknown (non-authoritative)"
            )
            emit_event(
                on_event, "failed", {"subtype": "timeout", "errors": [error]}
            )
            return HarnessResult(
                session_id=session_id,
                files_changed=files_changed,
                cost_usd=None,
                exit_status="timeout",
                error=error,
            )
        error = (
            f"claude CLI exited {returncode} without a result event: "
            f"{stderr_tail or '(no stderr)'}"
        )
        emit_event(on_event, "failed", {"subtype": "error", "errors": [error]})
        return HarnessResult(
            session_id=session_id,
            files_changed=files_changed,
            cost_usd=None,
            exit_status="error",
            error=error,
        )
