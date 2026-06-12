"""opencode loop vehicle — ``opencode run --format json`` sessions.

Custom API-key ModelSources ride this vehicle (PRD D1): opencode routes to
any models.dev provider, so one adapter covers OpenAI/Google/GLM/Kimi/local.
Flags and event schema follow the verified research at
``.trellis/tasks/06-11-harness-execution-leg/research/multi-backend-runners.md``
(opencode 1.1.42; shapes from ``run.ts`` / ``types.gen.ts``).

Research notes baked in:

- ``--format json`` emits JSONL events ``step_start | step_finish |
  tool_use | text | reasoning | error``; ``step_finish.part.cost`` is REAL
  USD per step (priced from the models.dev catalog). There is no final
  aggregate event in subprocess mode, so the run cost is the sum of the
  per-step reals — authoritative, kept even when the run is killed.
- No native budget cap → external :class:`BudgetMeter`; on breach the
  process group is killed and the run exits ``budget_exceeded``
  (research pitfall 9).
- Permissive permission defaults → a restrictive per-run
  ``OPENCODE_PERMISSION`` is injected via env, never inherited from user
  config (pitfall 6). ``external_directory: deny`` confines file tools to
  the workspace; bash/web tools (external side-effects) are ``deny`` in
  run mode — subprocess mode has no TTY to answer an ``ask`` prompt, so
  ``ask`` simply stalls the run. Interactive approvals for this vehicle
  (serve-mode ``permission.asked`` SSE + reply endpoint, PRD D5) are
  future work; until then denials surface in events/result like any
  other denial and the founder sees a ``blocked`` outcome.
- opencode loads ``~/.claude/CLAUDE.md`` + ``.claude/skills`` by default →
  ``OPENCODE_DISABLE_CLAUDE_CODE=true`` and CLAUDE* env scrubbed
  (pitfall 7); ``OPENCODE_DISABLE_AUTOUPDATE=true`` for determinism.
- ``--dir`` is not in 1.1.42 (docs drift, pitfall 11) → workspace scoping
  via cwd only.
- opencode has no turn-cap flag; ``caps.max_turns`` is not enforceable on
  this vehicle (budget cap is the binding limit).
"""

from __future__ import annotations

import json
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

# Restrictive per-run permission profile (research permissions doc):
# per-tool allow|ask|deny. Workspace-scoped read/edit work is allowed
# (external_directory deny enforces the scope); external side-effects
# (bash, web) are DENIED in run mode — ``ask`` stalls a TTY-less
# subprocess run (no one can answer). Serve-mode interactive approvals
# (PRD D5) are future work; doom_loop denied outright.
RESTRICTIVE_PERMISSION: dict[str, Any] = {
    "read": "allow",
    "glob": "allow",
    "grep": "allow",
    "lsp": "allow",
    "edit": "allow",
    "task": "allow",
    "skill": "allow",
    "question": "allow",
    "bash": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "external_directory": "deny",
    "doom_loop": "deny",
}

_MISSING_BINARY_HINT = (
    "opencode CLI not found on PATH — install opencode or switch the "
    "ModelSource to a subscription provider"
)


class OpencodeRunner:
    """API-key loop vehicle: custom API ModelSource → opencode CLI (PRD D1)."""

    def __init__(
        self,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # opencode model ids are "provider/model" (e.g. "openai/gpt-5").
        self._model = model
        self._timeout_seconds = timeout_seconds

    @property
    def vehicle_name(self) -> str:
        return "opencode"

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
        cmd = [
            "opencode",
            "run",
            prompt,
            "--format",
            "json",
            "--model",
            self._model,
        ]
        if resume_session_id is not None:
            cmd += ["--session", resume_session_id]
        return cmd

    @staticmethod
    def _env() -> dict[str, str]:
        return scrubbed_env(
            extra={
                "OPENCODE_DISABLE_CLAUDE_CODE": "true",
                "OPENCODE_DISABLE_AUTOUPDATE": "true",
                "OPENCODE_PERMISSION": json.dumps(RESTRICTIVE_PERMISSION),
            }
        )

    def _run(
        self,
        prompt: str,
        workspace: Path,
        caps: HarnessCaps,
        on_event: EventCallback | None,
        resume_session_id: str | None,
    ) -> HarnessResult:
        assert_workspace_safe(workspace)
        meter = BudgetMeter(caps.budget_cap_usd)

        session_id: str | None = None
        final_text = ""
        tokens_in = 0
        tokens_out = 0
        error_text: str | None = None

        def handle(event: dict[str, Any]) -> bool:
            nonlocal session_id, final_text, tokens_in, tokens_out, error_text
            sid = event.get("sessionID")
            if session_id is None and sid:
                session_id = str(sid)
                emit_event(
                    on_event,
                    "session_started",
                    {"session_id": session_id},
                    raw=event,
                )
            etype = event.get("type")
            part = event.get("part")
            if not isinstance(part, dict):
                part = {}
            if etype == "step_start":
                emit_event(on_event, "turn", {}, raw=event)
            elif etype == "text":
                text = str(part.get("text") or "")
                if text:
                    final_text = text  # last finished text part = final answer
                emit_event(on_event, "text", {"text": text})
            elif etype == "tool_use":
                state = part.get("state")
                if not isinstance(state, dict):
                    state = {}
                emit_event(
                    on_event,
                    "tool_use",
                    {
                        "tool_name": str(part.get("tool") or ""),
                        "tool_input": state.get("input") or {},
                        "status": state.get("status"),
                    },
                    raw=event,
                )
            elif etype == "step_finish":
                cost = float(part.get("cost") or 0.0)
                tokens = part.get("tokens")
                if not isinstance(tokens, dict):
                    tokens = {}
                tokens_in += int(tokens.get("input") or 0)
                tokens_out += int(tokens.get("output") or 0)
                emit_event(
                    on_event,
                    "cost_delta",
                    {"usd": cost, "tokens": tokens, "estimated": False},
                    raw=event,
                )
                if not meter.add(cost):
                    emit_event(
                        on_event,
                        "failed",
                        {
                            "subtype": "budget_exceeded",
                            "spent_usd": meter.spent_usd,
                            "cap_usd": caps.budget_cap_usd,
                        },
                    )
                    return False  # kill the process group
            elif etype == "error":
                err = event.get("error")
                if isinstance(err, dict):
                    error_text = str(
                        err.get("message") or err.get("name") or err
                    )
                else:
                    error_text = str(err) if err else json.dumps(event)
            return True

        outcome = run_streaming(
            cmd=self._build_command(prompt, resume_session_id),
            workspace=workspace,
            env=self._env(),
            timeout_seconds=self._timeout_seconds,
            handle_event=handle,
            missing_binary_hint=_MISSING_BINARY_HINT,
        )

        # Per-step reals sum to an authoritative total even when the run
        # was killed mid-flight (unlike the claude vehicle).
        cost = meter.spent_usd
        common = {
            "final_text": final_text,
            # A resume that dies before any event must not lose the known
            # session id — it stays resumable.
            "session_id": session_id or resume_session_id,
            "files_changed": git_files_changed(Path(workspace)),
            "cost_usd": cost,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
        if outcome.aborted:
            error = (
                f"budget cap exceeded: spent ${meter.spent_usd:.4f} of the "
                f"${caps.budget_cap_usd} cap — run killed (PRD: pause and "
                "route to approval)"
            )
            return HarnessResult(
                **common, exit_status="budget_exceeded", error=error
            )
        if outcome.timed_out:
            error = (
                f"opencode run exceeded the {self._timeout_seconds:.0f}s "
                "wall-clock timeout and was terminated"
            )
            emit_event(
                on_event, "failed", {"subtype": "timeout", "errors": [error]}
            )
            return HarnessResult(**common, exit_status="timeout", error=error)
        if error_text is not None or (outcome.returncode or 0) != 0:
            error = error_text or (
                f"opencode exited {outcome.returncode}: "
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
                "total_cost_usd": cost,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            },
        )
        return HarnessResult(**common, exit_status="success")
