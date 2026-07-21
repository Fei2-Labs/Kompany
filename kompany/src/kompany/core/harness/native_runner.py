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

from kompany.core.agent_tools.base import ToolContext
from kompany.core.agent_tools.registry import ToolRegistry
from kompany.core.agent_tools.workspace_tools import build_default_registry
from kompany.core.harness.native_tools import TOOL_DOCS
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

_NATIVE_TOOL_SYSTEM_PROMPT = """\
You are an autonomous software/work agent operating inside a git
workspace. Use the provided tools to inspect and modify the workspace and
to research on the web. Work only inside the workspace; use relative
paths. Call tools as needed; you will receive each tool's result before
your next step. When the task is complete, reply with a final summary and
no further tool calls.
"""

SESSION_DIR_NAME = ".native-session"
# Resume context window (D4): simple truncation, last N transcript turns.
RESUME_CONTEXT_TURNS = 30
# Default history-compression threshold (06-16 P5). When the in-loop
# message list grows past this many *turn-pairs*, older turns are folded
# into one rolling summary so long sessions stay token-bounded (the
# GenericAgent ~30K-context trick). 0 disables. Overridable per-run via
# ``settings.agentic_history_compress_after_turns``.
HISTORY_COMPRESS_AFTER_TURNS = 8
# How many of the most-recent turns to keep verbatim after a compression
# pass (the working checkpoint — latest state — is always preserved).
HISTORY_KEEP_RECENT_TURNS = 2

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

    def __init__(
        self,
        model: str,
        llm_client: Any,
        registry: ToolRegistry | None = None,
        settings: Any = None,
        engine: Any = None,
        system_prompt: str | None = None,
        tool_ctx: ToolContext | None = None,
        skip_workspace_safety: bool = False,
    ) -> None:
        self._model = model
        self._llm = llm_client
        # Pluggable tool registry (06-16 P1). Defaults to the built-in set
        # (4 workspace tools + web_fetch/web_search/file_patch). The
        # NativeRunner dispatches model tool calls through this instead of
        # the hardcoded ``native_tools.execute_tool``.
        self._registry = registry or build_default_registry()
        self._settings = settings
        self._engine = engine
        # 06-16 P2 agentic chat: an injected persona system prompt (the CEO
        # soul + agentic instructions) overrides the generic agent prompt,
        # and a caller-owned ToolContext lets the channel path read back
        # ``extra["pending_approval_ids"]`` after the run (inline gate).
        # ``skip_workspace_safety`` lets the chat loop run on a scratch
        # (non-git, non-source) workspace without the constitution guard
        # that is meant for project/code sessions.
        self._system_prompt_override = system_prompt
        self._tool_ctx_override = tool_ctx
        self._skip_workspace_safety = skip_workspace_safety

    @property
    def vehicle_name(self) -> str:
        return "native"

    def _supports_native_tools(self) -> bool:
        """Whether the wired model/provider can do native tool_use."""
        check = getattr(self._llm, "supports_native_tools", None)
        if not callable(check):
            return False
        try:
            return bool(check(self._model))
        except Exception:  # noqa: BLE001 — capability probe must not crash
            return False

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
        if not self._skip_workspace_safety:
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

        # Native tool_use path when the provider supports it; otherwise
        # fall back to the legacy JSON-injection protocol below (CLI /
        # non-tool providers). Both share session/transcript + caps + cost.
        if self._supports_native_tools():
            return self._run_native_tools(
                prompt, workspace, caps, on_event,
                session_id, transcript_path, history,
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
                entry["observation"] = self._registry.dispatch(
                    turn.tool.name, turn.tool.args, self._tool_ctx(workspace)
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

    # ------------------------------------------------------------------
    # native tool_use loop (06-16 P1)
    # ------------------------------------------------------------------

    def _tool_ctx(self, workspace: Path) -> ToolContext:
        if self._tool_ctx_override is not None:
            return self._tool_ctx_override
        return ToolContext(
            workspace=workspace,
            settings=self._settings,
            engine=self._engine,
        )

    def _native_tool_system_prompt(self) -> str:
        return self._system_prompt_override or _NATIVE_TOOL_SYSTEM_PROMPT

    def _compress_after_turns(self) -> int:
        """Per-run history-compression threshold (0 disables)."""
        val = getattr(
            self._settings, "agentic_history_compress_after_turns", None
        )
        if val is None:
            return HISTORY_COMPRESS_AFTER_TURNS
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            return HISTORY_COMPRESS_AFTER_TURNS

    @staticmethod
    def _is_seed(msg: dict[str, Any]) -> bool:
        """The very first user message (the task seed) — never compressed."""
        content = msg.get("content")
        return (
            msg.get("role") == "user"
            and isinstance(content, str)
            and content.startswith("Task:")
        )

    def _maybe_compress_history(
        self, messages: list[dict[str, Any]], history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Fold older turns into one rolling summary, keeping the checkpoint.

        GenericAgent's token-efficiency trick: when the running transcript
        grows past the threshold, the older assistant/tool turns are replaced
        by a single compact summary message (built from the persisted
        transcript ``history``) so the context window stays bounded. The task
        seed and the most-recent turns (the working checkpoint — latest
        state) are preserved verbatim, so the model never loses where it is.

        Returns the (possibly) rebuilt message list. A no-op below threshold.
        """
        threshold = self._compress_after_turns()
        if threshold <= 0 or len(history) <= threshold:
            return messages
        keep = HISTORY_KEEP_RECENT_TURNS
        older = history[:-keep] if keep else list(history)
        recent = history[-keep:] if keep else []
        if not older:
            return messages

        summary_lines = [
            "Summary of earlier turns (compressed to save context):",
        ]
        for e in older:
            thought = str(e.get("thought") or "").strip()
            tool = e.get("tool") or {}
            names = tool.get("names") if isinstance(tool, dict) else None
            obs = str(e.get("observation") or "").strip()
            piece = f"- turn {e.get('turn', '?')}"
            if names:
                piece += f": called {', '.join(names)}"
            elif thought:
                piece += f": {thought[:160]}"
            if obs:
                piece += f" -> {obs[:200]}"
            summary_lines.append(piece)
        summary = "\n".join(summary_lines)

        # Rebuild: [task seed] + [one summary user msg] + [recent turns
        # verbatim as a compact recap]. The latest checkpoint is intact.
        rebuilt: list[dict[str, Any]] = []
        seed = next((m for m in messages if self._is_seed(m)), None)
        if seed is not None:
            rebuilt.append(seed)
        rebuilt.append({"role": "user", "content": summary})
        recap = self._render_history(recent)
        if recap:
            rebuilt.append({"role": "user", "content": recap})
        return rebuilt

    def _run_native_tools(
        self,
        prompt: str,
        workspace: Path,
        caps: HarnessCaps,
        on_event: EventCallback | None,
        session_id: str,
        transcript_path: Path,
        history: list[dict[str, Any]],
    ) -> HarnessResult:
        """Agentic loop driven by provider-native tool_use.

        Replaces the brittle JSON ``NativeTurn`` protocol with real
        ``call_with_tools`` turns: the model emits ``tool_use`` blocks,
        the registry dispatches them (READ inline, EXTERNAL gated), and
        observations are fed back as ``tool_result`` messages. Transcript
        persistence, turn caps, budget cap and cost tracking are all
        preserved.
        """
        total_cost = 0.0
        tokens_in = 0
        tokens_out = 0
        budget_cap = caps.budget_cap_usd
        start_turn = (history[-1].get("turn", 0) + 1) if history else 1
        ctx = self._tool_ctx(workspace)
        specs = self._registry.specs()

        # Provider-native message list. Seed with the prior transcript as
        # a compact text recap (resume) plus the task. The provider mixin
        # prepends the system prompt for OpenAI; Anthropic takes it
        # separately via call_with_tools(system=...).
        recap = self._render_history(history)
        seed = f"Task:\n{prompt}"
        if recap:
            seed += f"\n\n{recap}"
        messages: list[dict[str, Any]] = [{"role": "user", "content": seed}]

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
            # P5 history compression: fold older turns into a rolling summary
            # once the transcript grows past the threshold, keeping the task
            # seed + latest checkpoint intact so long sessions stay bounded.
            messages = self._maybe_compress_history(messages, history)
            try:
                resp = self._llm.call_with_tools(
                    model=self._model,
                    system=self._native_tool_system_prompt(),
                    messages=messages,
                    tools=specs,
                    agent_name="native_runner",
                    action_type="native_runner",
                    tool_choice="auto",
                )
            except Exception as exc:  # noqa: BLE001 — work preserved
                emit_event(on_event, "failed", {"error": str(exc)})
                return _result("error", error=f"{type(exc).__name__}: {exc}")

            total_cost += float(getattr(resp, "cost_usd", 0.0) or 0.0)
            tokens_in += int(getattr(resp, "input_tokens", 0) or 0)
            tokens_out += int(getattr(resp, "output_tokens", 0) or 0)
            tool_calls = list(getattr(resp, "tool_calls", []) or [])

            emit_event(
                on_event,
                "turn",
                {"turn": turn_no, "thought": resp.text,
                 "cost_usd": total_cost},
            )

            entry: dict[str, Any] = {
                "turn": turn_no,
                "thought": resp.text,
                "tool": None,
                "done": not tool_calls,
                "final_text": resp.text if not tool_calls else None,
                "observation": None,
            }

            # No tool calls -> the model is done; its text is the answer.
            if not tool_calls:
                final_text = resp.text
                self._append_transcript(transcript_path, entry)
                emit_event(on_event, "text", {"text": final_text})
                emit_event(
                    on_event,
                    "completed",
                    {"session_id": session_id, "cost_usd": total_cost},
                )
                return _result("success", final_text=final_text)

            # Replay the assistant turn verbatim, then append the tool
            # results in the provider's native shape.
            assistant_msg = getattr(resp, "raw_assistant_message", None)
            if assistant_msg is not None:
                messages.append(assistant_msg)

            observations: list[str] = []
            results_blocks: list[dict[str, Any]] = []
            openai_tool_msgs: list[dict[str, Any]] = []
            for call in tool_calls:
                emit_event(
                    on_event,
                    "tool_use",
                    {"tool_name": call.name, "args": call.arguments},
                )
                obs = self._registry.dispatch(call.name, call.arguments, ctx)
                observations.append(f"{call.name}: {obs}")
                results_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": call.call_id,
                    "content": obs,
                })
                openai_tool_msgs.append({
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "content": obs,
                })

            # Feed observations back in the right wire shape. Anthropic
            # wants one user message of tool_result blocks; OpenAI wants
            # one role:"tool" message per call.
            if assistant_msg is not None and assistant_msg.get("content") and isinstance(
                assistant_msg.get("content"), list
            ):
                # Anthropic assistant content is a list of blocks.
                messages.append({"role": "user", "content": results_blocks})
            else:
                messages.extend(openai_tool_msgs)

            entry["tool"] = {
                "names": [c.name for c in tool_calls],
            }
            entry["observation"] = "\n".join(observations)
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
