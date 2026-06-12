"""codex loop vehicle — ``codex exec --json`` headless sessions.

OpenAI-subscription ModelSources ride this vehicle (PRD D1): ChatGPT-login
compute cannot leave the codex CLI. Flags and event schema follow the
verified research at
``.trellis/tasks/06-11-harness-execution-leg/research/multi-backend-runners.md``
(codex 0.133.0).

Research notes baked in:

- ``--json`` turns stdout into JSONL: ``thread.started`` (carries
  ``thread_id`` — persisted as the session id), ``turn.started``,
  ``item.started/updated/completed``, ``turn.completed`` (token usage),
  ``turn.failed``, ``error``. Progress otherwise goes to stderr
  (pitfall 2: never merge the pipes).
- Cost is tokens-only — USD is ESTIMATED here from the pricing table and
  flagged via ``HarnessResult.cost_is_estimate`` (pitfall 8,
  evidence-traced discipline). Cached input tokens are billed at a
  discounted rate (research caveat on cached pricing).
- No native budget cap → external :class:`BudgetMeter` over the estimated
  deltas; breach kills the process group → ``budget_exceeded``
  (pitfall 9).
- codex refuses non-git dirs (pitfall 3) — PR1 workspaces always
  ``git init``; asserted here anyway with a clear error rather than
  passing ``--skip-git-repo-check``.
- Sandbox is OS-level (Seatbelt/Landlock); ``--sandbox workspace-write``
  set explicitly every run (pitfall 1: never rely on aliases/defaults).
  ``--ephemeral`` is never used — it kills resume (pitfall 4).
- Resume: ``codex exec resume <thread_id> "<prompt>"``. The resume
  subcommand does not accept ``--sandbox`` (verified against
  ``codex exec resume --help``); the sandbox is pinned there via
  ``-c sandbox_mode="workspace-write"`` instead.
- codex exec has no turn-cap flag; ``caps.max_turns`` is not enforceable
  on this vehicle (budget cap is the binding limit).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kompany.core.harness.budget import BudgetMeter
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
from kompany.llm.models import estimate_cost

# OpenAI bills cached input below the standard input rate (model-dependent,
# 10–50%). The pricing table has no cached tier, so cached tokens are
# approximated at this fraction of the input rate — acceptable because the
# whole figure is flagged as an estimate (cost_is_estimate=True).
CACHED_INPUT_RATE = 0.25

# Item types that represent tool activity (vs. plain agent text).
_TOOL_ITEM_TYPES = frozenset(
    {"command_execution", "file_change", "mcp_tool_call", "web_search"}
)

_MISSING_BINARY_HINT = (
    "codex CLI not found on PATH — install codex or switch the "
    "ModelSource away from the OpenAI subscription"
)


def estimate_turn_cost(model: str, usage: dict[str, Any]) -> float:
    """Estimated USD for one ``turn.completed.usage`` payload.

    ``input_tokens`` includes ``cached_input_tokens``; the cached share is
    billed at :data:`CACHED_INPUT_RATE` of the input rate.
    ``reasoning_output_tokens`` is already part of ``output_tokens``.
    """
    tokens_in = int(usage.get("input_tokens") or 0)
    cached = min(int(usage.get("cached_input_tokens") or 0), tokens_in)
    tokens_out = int(usage.get("output_tokens") or 0)
    uncached_usd = estimate_cost(model, tokens_in - cached, tokens_out)
    cached_usd = estimate_cost(model, cached, 0) * CACHED_INPUT_RATE
    return uncached_usd + cached_usd


def _assert_git_workspace(workspace: Path) -> None:
    if not (Path(workspace) / ".git").exists():
        raise ValueError(
            f"codex requires a git workspace and {workspace} has no .git — "
            "create the workspace via ensure_workspace() (it always git-inits)"
        )


class CodexRunner:
    """OpenAI-subscription loop vehicle: ChatGPT login → codex CLI (PRD D1)."""

    def __init__(
        self,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # Model name doubles as the pricing-table key for cost estimates.
        self._model = model
        self._timeout_seconds = timeout_seconds

    @property
    def vehicle_name(self) -> str:
        return "codex"

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
        return build_handoff_brief(result, self.vehicle_name)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _build_command(
        self, prompt: str, resume_session_id: str | None
    ) -> list[str]:
        cmd = ["codex", "exec"]
        if resume_session_id is not None:
            # `codex exec resume --help` (0.133.0) accepts --json/--model/-c
            # but NOT -s/--sandbox — the sandbox must ride the equivalent
            # config override on resume runs.
            cmd += ["resume", resume_session_id]
            sandbox = ["-c", 'sandbox_mode="workspace-write"']
        else:
            sandbox = ["--sandbox", "workspace-write"]
        cmd += ["--json", *sandbox, "--model", self._model, prompt]
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
        _assert_git_workspace(workspace)
        meter = BudgetMeter(caps.budget_cap_usd)

        session_id: str | None = None
        final_text = ""
        tokens_in = 0
        tokens_out = 0
        error_text: str | None = None

        def handle(event: dict[str, Any]) -> bool:
            nonlocal session_id, final_text, tokens_in, tokens_out, error_text
            etype = event.get("type")
            if etype == "thread.started":
                session_id = str(event.get("thread_id") or "") or None
                emit_event(
                    on_event,
                    "session_started",
                    {"session_id": session_id},
                    raw=event,
                )
            elif etype == "turn.started":
                emit_event(on_event, "turn", {}, raw=event)
            elif etype == "item.completed":
                item = event.get("item")
                if not isinstance(item, dict):
                    item = {}
                item_type = str(item.get("item_type") or item.get("type") or "")
                if item_type == "agent_message":
                    text = str(item.get("text") or "")
                    if text:
                        final_text = text  # last agent message = final answer
                    emit_event(on_event, "text", {"text": text})
                elif item_type in _TOOL_ITEM_TYPES:
                    emit_event(
                        on_event,
                        "tool_use",
                        {
                            "tool_name": item_type,
                            "tool_input": {
                                k: item[k]
                                for k in ("command", "changes", "server", "tool")
                                if k in item
                            },
                            "status": item.get("status") or "completed",
                        },
                        raw=event,
                    )
            elif etype == "turn.completed":
                usage = event.get("usage")
                if not isinstance(usage, dict):
                    usage = {}
                tokens_in += int(usage.get("input_tokens") or 0)
                tokens_out += int(usage.get("output_tokens") or 0)
                usd = estimate_turn_cost(self._model, usage)
                emit_event(
                    on_event,
                    "cost_delta",
                    {"usd": usd, "tokens": usage, "estimated": True},
                    raw=event,
                )
                if not meter.add(usd):
                    emit_event(
                        on_event,
                        "failed",
                        {
                            "subtype": "budget_exceeded",
                            "spent_usd": meter.spent_usd,
                            "cap_usd": caps.budget_cap_usd,
                            "estimated": True,
                        },
                    )
                    return False  # kill the process group
            elif etype == "turn.failed" or etype == "error":
                err = event.get("error")
                if isinstance(err, dict):
                    error_text = str(err.get("message") or err)
                else:
                    error_text = str(
                        err or event.get("message") or "codex run failed"
                    )
            return True

        outcome = run_streaming(
            cmd=self._build_command(prompt, resume_session_id),
            workspace=workspace,
            env=scrubbed_env(),
            timeout_seconds=self._timeout_seconds,
            handle_event=handle,
            missing_binary_hint=_MISSING_BINARY_HINT,
        )

        # Cost is the accumulated estimate — flagged, never authoritative.
        common = {
            "final_text": final_text,
            # A resume that dies before any event must not lose the known
            # session id — it stays resumable.
            "session_id": session_id or resume_session_id,
            "files_changed": git_files_changed(Path(workspace)),
            "cost_usd": meter.spent_usd,
            "cost_is_estimate": True,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
        if outcome.aborted:
            error = (
                f"budget cap exceeded: estimated spend ${meter.spent_usd:.4f} "
                f"of the ${caps.budget_cap_usd} cap — run killed (PRD: pause "
                "and route to approval)"
            )
            return HarnessResult(
                **common, exit_status="budget_exceeded", error=error
            )
        if outcome.timed_out:
            error = (
                f"codex exec exceeded the {self._timeout_seconds:.0f}s "
                "wall-clock timeout and was terminated"
            )
            emit_event(
                on_event, "failed", {"subtype": "timeout", "errors": [error]}
            )
            return HarnessResult(**common, exit_status="timeout", error=error)
        if error_text is not None or (outcome.returncode or 0) != 0:
            error = error_text or (
                f"codex exited {outcome.returncode}: "
                f"{outcome.stderr_tail or '(no stderr)'}"
            )
            emit_event(
                on_event, "failed", {"subtype": "error", "errors": [error]}
            )
            return HarnessResult(**common, exit_status="error", error=error)
        emit_event(
            on_event,
            "completed",
            {
                "total_cost_usd": meter.spent_usd,
                "cost_is_estimate": True,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            },
        )
        return HarnessResult(**common, exit_status="success")
