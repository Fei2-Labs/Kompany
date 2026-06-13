"""NativeRunner — Kompany-owned agentic loop as a HarnessRunner vehicle.

Issue #20 (PRD: the internal design spec). Same protocol as
the rented vehicles (claude/codex/opencode), but the loop is ours:
LLMClient structured tool-calls (D2), workspace-scoped file/shell tools
only (D3 — no registry tools in-loop), transcript JSONL persistence with
resume (D4). Every model call books through the normal LLMClient cost
path (action_type="native_runner"); cumulative real cost enforces
``caps.budget_cap_usd`` and the turn count enforces ``caps.max_turns``.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from kompany.core.harness.native_tools import TOOL_DOCS, execute_tool
from kompany.core.harness.protocol import (
    EventCallback,
    HandoffBrief,
    HarnessCaps,
    HarnessResult,
)
from kompany.core.harness.runner_utils import build_handoff_brief, emit_event
from kompany.core.harness.safety import assert_workspace_safe
from kompany.core.harness.workspace import git_files_changed

__all__ = ["NativeRunner", "NativeTurn", "SESSION_DIR_NAME"]

SESSION_DIR_NAME = ".native-session"
# Resume context window (D4): simple truncation, last N transcript turns.
RESUME_CONTEXT_TURNS = 30

_SYSTEM_PROMPT = f"""\
You are an autonomous software/work agent operating inside a git
workspace. You work in turns: each turn you respond with JSON containing
your thought, optionally ONE tool call, and whether you are done.

Rules:
- Work only inside the workspace; relative paths only.
- Call at most one tool per turn; you will receive its observation next turn.
- When the task is complete, set done=true and put your summary in
  final_text (no tool call needed on the final turn).

{TOOL_DOCS}"""


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class NativeTurn(BaseModel):
    """One loop step from the model (PRD D2)."""

    thought: str = ""
    tool: ToolCall | None = None
    done: bool = False
    final_text: str | None = None


class NativeRunner:
    """Kompany-owned loop vehicle (engine-internal, never founder-facing)."""

    def __init__(self, model: str, llm_client: Any) -> None:
        self._model = model
        self._llm = llm_client

    @property
    def vehicle_name(self) -> str:
        return "native"

    # ------------------------------------------------------------------
    # protocol
    # ------------------------------------------------------------------

    def start(
        self,
        prompt: str,
        workspace: Path,
        caps: HarnessCaps,
        on_event: EventCallback | None = None,
    ) -> HarnessResult:
        return self._run(prompt, workspace, caps, on_event, session_id=None)

    def resume(
        self,
        session_id: str,
        prompt: str,
        workspace: Path,
        caps: HarnessCaps,
        on_event: EventCallback | None = None,
    ) -> HarnessResult:
        return self._run(
            prompt, workspace, caps, on_event, session_id=session_id
        )

    def handoff(self, result: HarnessResult) -> HandoffBrief:
        return build_handoff_brief(result, self.vehicle_name)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    @staticmethod
    def _transcript_path(workspace: Path, session_id: str) -> Path:
        return Path(workspace) / SESSION_DIR_NAME / f"{session_id}.jsonl"

    @staticmethod
    def _load_transcript(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries[-RESUME_CONTEXT_TURNS:]

    @staticmethod
    def _append_transcript(path: Path, entry: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def _render_history(entries: list[dict[str, Any]]) -> str:
        if not entries:
            return ""
        lines = ["Transcript so far (oldest first):"]
        for e in entries:
            lines.append(f"[turn {e.get('turn', '?')}] thought: {e.get('thought', '')}")
            tool = e.get("tool")
            if tool:
                lines.append(
                    f"  tool: {tool.get('name')} args={json.dumps(tool.get('args', {}), ensure_ascii=False)}"
                )
            obs = e.get("observation")
            if obs:
                lines.append(f"  observation: {obs}")
            if e.get("final_text"):
                lines.append(f"  final: {e['final_text']}")
        return "\n".join(lines)

    def _run(
        self,
        prompt: str,
        workspace: Path,
        caps: HarnessCaps,
        on_event: EventCallback | None,
        session_id: str | None,
    ) -> HarnessResult:
        workspace = Path(workspace)
        assert_workspace_safe(workspace)

        resumed = session_id is not None
        session_id = session_id or uuid.uuid4().hex
        transcript_path = self._transcript_path(workspace, session_id)
        history = self._load_transcript(transcript_path) if resumed else []

        emit_event(
            on_event,
            "session_started",
            {"session_id": session_id, "vehicle": self.vehicle_name,
             "resumed": resumed},
        )

        total_cost = 0.0
        tokens_in = 0
        tokens_out = 0
        budget_cap = caps.budget_cap_usd
        start_turn = (history[-1].get("turn", 0) + 1) if history else 1

        def _result(exit_status: str, final_text: str = "",
                    error: str | None = None) -> HarnessResult:
            return HarnessResult(
                final_text=final_text,
                session_id=session_id,
                files_changed=git_files_changed(workspace),
                cost_usd=total_cost,
                cost_is_estimate=False,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                exit_status=exit_status,
                error=error,
            )

        for turn_no in range(start_turn, start_turn + caps.max_turns):
            user_prompt = (
                f"Task:\n{prompt}\n\n{self._render_history(history)}".strip()
                + "\n\nRespond with your next turn."
            )
            try:
                resp = self._llm.call_structured(
                    model=self._model,
                    system=_SYSTEM_PROMPT,
                    prompt=user_prompt,
                    output_schema=NativeTurn,
                    agent_name="native_runner",
                    action_type="native_runner",
                )
            except Exception as exc:  # noqa: BLE001 — work preserved
                emit_event(on_event, "failed", {"error": str(exc)})
                return _result(
                    "error", error=f"{type(exc).__name__}: {exc}"
                )
            turn: NativeTurn = resp.parsed
            total_cost += float(getattr(resp, "cost_usd", 0.0) or 0.0)
            tokens_in += int(getattr(resp, "input_tokens", 0) or 0)
            tokens_out += int(getattr(resp, "output_tokens", 0) or 0)

            emit_event(
                on_event,
                "turn",
                {"turn": turn_no, "thought": turn.thought,
                 "cost_usd": total_cost},
            )

            entry: dict[str, Any] = {
                "turn": turn_no,
                "thought": turn.thought,
                "tool": turn.tool.model_dump() if turn.tool else None,
                "done": turn.done,
                "final_text": turn.final_text,
                "observation": None,
            }

            if turn.done:
                final_text = turn.final_text or turn.thought
                self._append_transcript(transcript_path, entry)
                emit_event(on_event, "text", {"text": final_text})
                emit_event(
                    on_event,
                    "completed",
                    {"session_id": session_id, "cost_usd": total_cost},
                )
                return _result("success", final_text=final_text)

            if turn.tool is not None:
                emit_event(
                    on_event,
                    "tool_use",
                    {"tool_name": turn.tool.name, "args": turn.tool.args},
                )
                entry["observation"] = execute_tool(
                    workspace, turn.tool.name, turn.tool.args
                )
            else:
                entry["observation"] = (
                    "No tool called and done=false — call a tool or set "
                    "done=true with final_text."
                )
            self._append_transcript(transcript_path, entry)
            history.append(entry)

            if budget_cap is not None and total_cost >= budget_cap:
                emit_event(
                    on_event,
                    "failed",
                    {"reason": "budget_exceeded", "cost_usd": total_cost,
                     "budget_cap_usd": budget_cap},
                )
                return _result(
                    "budget_exceeded",
                    error=(
                        f"budget cap ${budget_cap:.2f} reached "
                        f"(spent ${total_cost:.2f})"
                    ),
                )

        emit_event(
            on_event,
            "failed",
            {"reason": "error_max_turns", "max_turns": caps.max_turns},
        )
        return _result(
            "error_max_turns",
            error=f"turn cap {caps.max_turns} reached without done=true",
        )
