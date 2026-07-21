"""Agentic CEO chat path (06-16-agentic-chat-engine P2).

Turns the board chat into a real agentic session: an ``answer``/``execute``
request that needs real work runs the upgraded :class:`NativeRunner` loop AS
the CEO — persona system prompt + in-loop tools + streaming + the inline
approval gate — instead of a single ``ceo.answer()``/handler LLM call.

The external contract is preserved: the loop's :class:`HarnessResult` is
mapped back onto a :class:`DirectiveResult` with the same status vocabulary
(``completed`` / ``gated`` / ``failed``), session semantics, and the
``run_id`` / ``session_id`` / ``total_ai_cost`` keys the board expects.

Streaming uses the existing rails: a :class:`ChatEventMonitor` publishes each
loop step to the EventHub (``harness.event`` with ``activity_kind:"chat"``)
for the board activity timeline AND appends ``progress_summary`` turns to the
channel thread so the chat shows the agent working, not just a final answer.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from kompany.core.agent_tools.base import ToolContext
from kompany.core.agent_tools.chat_registry import build_chat_registry
from kompany.core.directive import Directive, DirectiveResult, DirectiveStatus
from kompany.core.event_hub import get_event_hub
from kompany.core.harness import HarnessEvent
from kompany.core.harness.protocol import HarnessCaps
from kompany.core.harness.native_runner import NativeRunner
from kompany.state.models import Decision, SessionStatus


_AGENTIC_INSTRUCTIONS = """\
You are operating as an AGENTIC executive: you have tools and you USE them to
actually do the work in this conversation, not just describe it. Rules:
- Call tools (web search/fetch, integrations) to gather facts and act. Do not
  guess when a tool can get the truth.
- Side-effecting / spending tools (sending email, paying) are GATED: when you
  call one it is routed to the founder for approval and does NOT run now. If a
  tool result says it is "PENDING founder approval", STOP calling it, tell the
  founder it is awaiting their approval, and summarize what will happen on
  approval.
- When you have done the work, give a final answer with no further tool calls.
- Speak in the first person as the company's CEO. Be concrete and honest.
"""


class ChatEventMonitor:
    """``on_event`` consumer for one agentic chat run.

    Mirrors the project-runner :class:`EventMonitor` rails but targets the
    chat surface: every normalized event is republished to the EventHub as
    ``harness.event`` with ``activity_kind:"chat"`` (board timeline), and
    tool-use / thought steps are appended to the channel thread as
    ``progress_summary`` turns so the conversation shows the agent working.
    """

    def __init__(self, engine: Any, session_id: str, directive_id: str | None,
                 hub: Any = None) -> None:
        self._engine = engine
        self._session_id = session_id
        self._directive_id = directive_id
        self._hub = hub if hub is not None else get_event_hub()
        self.steps = 0
        # Tool names seen this run — feeds the P5 crystallization gate
        # (a session below the tool-use floor is not worth a skill).
        self.tool_names: list[str] = []

    def __call__(self, event: HarnessEvent) -> None:
        self._publish(event)
        if event.kind == "tool_use":
            self.steps += 1
            name = str(event.payload.get("tool_name") or "").strip()
            if name:
                self.tool_names.append(name)
            self._thread(
                f"🔧 Using {event.payload.get('tool_name', 'a tool')}…"
            )
        elif event.kind == "turn":
            thought = str(event.payload.get("thought") or "").strip()
            if thought:
                self._thread(f"💭 {thought[:300]}")

    def _publish(self, event: HarnessEvent) -> None:
        try:
            self._hub.publish(
                "harness.event",
                {
                    "activity_kind": "chat",
                    "session_id": self._session_id,
                    "agent_role": "ceo",
                    "kind": event.kind,
                    "summary": self._summarize(event),
                },
            )
        except Exception:  # noqa: BLE001 — live feed is best-effort
            pass

    def _thread(self, content: str) -> None:
        try:
            self._engine.channel.add_turn(
                self._session_id,
                role="ceo",
                content=content,
                kind="progress_summary",
                directive_id=self._directive_id,
            )
        except Exception:  # noqa: BLE001 — thread streaming is best-effort
            pass

    @staticmethod
    def _summarize(event: HarnessEvent) -> str:
        if event.kind == "tool_use":
            return str(event.payload.get("tool_name") or "")[:120]
        if event.kind == "text":
            return str(event.payload.get("text") or "")[:120]
        if event.kind == "turn":
            return str(event.payload.get("thought") or "")[:120]
        return event.kind


class AgenticChatMixin:
    """Run the agentic CEO loop from the channel path (06-16 P2)."""

    def _agentic_chat_available(self) -> bool:
        """Whether a real request should run the agentic loop.

        Requires the feature flag AND a native-tool-capable model (Anthropic
        / OpenAI-compatible). When the model can't do native tool_use we fall
        back to the legacy single-call path so the board contract is never
        broken (CLI-only / unconfigured setups keep working unchanged).
        """
        if not getattr(self.settings, "agentic_chat_enabled", False):
            return False
        try:
            model = self.settings.get_model_for_tier("apex")
            return bool(self.llm.supports_native_tools(model))
        except Exception:  # noqa: BLE001 — capability probe must not crash
            return False

    def _agentic_chat_system_prompt(self, ceo, request: str | None = None) -> str:
        """Compose the CEO persona soul with the agentic tool instructions.

        P5: retrieve skills matching ``request`` by trigger words and inject
        them as a token-bounded "relevant past skills" block so the CEO
        reuses prior solutions. No embeddings — plain trigger-word overlap.
        """
        persona = ceo.with_founder_context(ceo.system_prompt())
        company_context, _ = self._compose_answer_context()
        prompt = (
            f"{persona}\n\n"
            f"{_AGENTIC_INSTRUCTIONS}\n\n"
            f"Current company context:\n{company_context}"
        )
        skills_block = self._retrieve_skills_block(request)
        if skills_block:
            prompt += f"\n\n{skills_block}"
        return prompt

    def _retrieve_skills_block(self, request: str | None) -> str:
        """Trigger-word-retrieved skills for the system prompt (P5)."""
        skills = getattr(self, "skills", None)
        if skills is None or not request:
            return ""
        try:
            limit = int(getattr(self.settings, "skill_retrieve_limit", 3))
            return skills.retrieve_text("ceo", request, limit=limit)
        except Exception:  # noqa: BLE001 — retrieval never blocks chat
            return ""

    def _agentic_workspace(self) -> Path:
        """A scratch (non-git, non-source) workspace for the chat loop.

        The chat loop does not edit a project checkout, but the NativeRunner
        still writes its session transcript under the workspace. A dedicated
        ``chat-sessions`` dir under ``data_dir`` keeps it isolated from any
        real project and off the Kompany source tree.
        """
        ws = Path(self.settings.data_dir) / "chat-sessions"
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    def _handle_agentic_chat(
        self,
        directive: Directive,
        classification,
        ceo,
        session_id: str,
        start_time: float,
        seed_context: str | None = None,
    ) -> DirectiveResult:
        """Run the agentic CEO loop in-conversation and map the result.

        - Builds the chat tool registry (web tools inline + integration tools
          gated) and a caller-owned :class:`ToolContext` so the inline gate's
          created approval ids are readable after the run.
        - Streams steps via :class:`ChatEventMonitor`.
        - Maps :class:`HarnessResult` → :class:`DirectiveResult`: a gated tool
          (pending approval) yields ``status="gated"`` + the approval id and
          parks the session; otherwise ``status="completed"`` and the session
          closes ``ANSWERED``. Failures yield ``status="failed"``.
        """
        self.agent_status.set("ceo", "thinking", "running agentic chat")
        registry = build_chat_registry(self)
        tool_ctx = ToolContext(
            workspace=None,
            settings=self.settings,
            engine=self,
            extra={"pending_approval_ids": []},
        )
        runner = NativeRunner(
            model=self.settings.get_model_for_tier("apex"),
            llm_client=self.llm,
            registry=registry,
            settings=self.settings,
            engine=self,
            system_prompt=self._agentic_chat_system_prompt(
                ceo, request=directive.raw_input
            ),
            tool_ctx=tool_ctx,
            skip_workspace_safety=True,
        )
        caps = HarnessCaps(
            budget_cap_usd=float(
                getattr(self.settings, "agentic_chat_budget_cap_usd", 0.50)
            ),
            max_turns=int(
                getattr(self.settings, "agentic_chat_max_turns", 16)
            ),
        )
        monitor = ChatEventMonitor(self, session_id, directive.id)

        prompt = directive.raw_input
        if seed_context:
            prompt = f"Conversation so far:\n{seed_context}\n\nFounder: {prompt}"

        try:
            result = runner.start(
                prompt=prompt,
                workspace=self._agentic_workspace(),
                caps=caps,
                on_event=monitor,
            )
        finally:
            # Close the per-run Edge-CDP browser session cleanly, if one was
            # opened (detaches from the founder's Edge, never closes it).
            try:
                from kompany.core.agent_tools.browser_tools import (
                    close_browser_session,
                )

                close_browser_session(tool_ctx)
            except Exception:  # noqa: BLE001 — cleanup is best-effort
                pass

        cost = float(result.cost_usd or 0.0)
        pending = list(tool_ctx.extra.get("pending_approval_ids") or [])
        approval_id = pending[0] if pending else None
        self.audit.record(
            "directive.agentic_chat",
            "CEO ran an agentic chat session",
            detail={
                "exit_status": result.exit_status,
                "steps": monitor.steps,
                "cost_usd": cost,
                "pending_approvals": pending,
            },
            agent_role="ceo",
            directive_id=directive.id,
        )

        if result.exit_status not in ("success",) and not approval_id:
            return self._agentic_failed(
                directive, classification, session_id, start_time,
                result.error or result.exit_status, cost,
            )

        final_text = (result.final_text or "").strip() or (
            "I've finished working on that."
        )

        if approval_id:
            # A side-effecting tool was proposed mid-chat — the session is a
            # parked founder decision. Surface it as ``gated`` so the board's
            # Needs-You can approve and the action runs for real then.
            directive.status = DirectiveStatus.AWAITING_APPROVAL
            status = "gated"
            session_state = SessionStatus.GATED
        else:
            directive.status = DirectiveStatus.COMPLETED
            status = "completed"
            session_state = SessionStatus.ANSWERED
            # P5: crystallize this successful, non-trivial session into a
            # reusable skill so the next similar request reuses it. Gated +
            # best-effort: never breaks the chat (failed/gated runs and
            # trivial one-tool chats are skipped inside the mixin).
            try:
                self.maybe_crystallize_skill(
                    request=directive.raw_input,
                    final_text=final_text,
                    tool_names=list(monitor.tool_names),
                    succeeded=True,
                    session_id=session_id,
                )
            except Exception:  # noqa: BLE001 — crystallization is best-effort
                pass

        result_obj = DirectiveResult(
            directive=directive,
            status=status,
            message=final_text,
            session_id=session_id,
            approval_id=approval_id,
            total_ai_cost=cost,
            agents_used=["ceo"],
        )
        self.channel.add_turn(
            session_id,
            role="ceo",
            content=final_text,
            kind="final",
            cost=cost,
            directive_id=directive.id,
        )
        self.channel.update_session_state(
            session_id,
            session_state,
            route="answer" if status == "completed" else "execute",
            directive_id=directive.id,
            approval_id=approval_id,
        )
        self._agentic_journal(
            directive, classification, result_obj, start_time
        )
        return result_obj

    def _agentic_failed(
        self, directive, classification, session_id, start_time, error, cost,
    ) -> DirectiveResult:
        message = (
            f"I hit a problem running that: {error}. Nothing external ran."
        )
        directive.status = DirectiveStatus.COMPLETED
        result_obj = DirectiveResult(
            directive=directive,
            status="failed",
            message=message,
            session_id=session_id,
            total_ai_cost=cost,
            agents_used=["ceo"],
        )
        self.channel.add_turn(
            session_id,
            role="ceo",
            content=message,
            kind="final",
            cost=cost,
            directive_id=directive.id,
        )
        self.channel.update_session_state(
            session_id,
            SessionStatus.ANSWERED,
            route="answer",
            directive_id=directive.id,
        )
        self._agentic_journal(
            directive, classification, result_obj, start_time
        )
        return result_obj

    def _agentic_journal(
        self, directive, classification, result_obj, start_time,
    ) -> None:
        self.journal.log(Decision(
            directive_id=directive.id,
            directive_type=(
                directive.directive_type.value
                if directive.directive_type else "unknown"
            ),
            raw_input=directive.raw_input,
            classification=(
                classification.model_dump() if classification else {}
            ),
            result={
                "status": result_obj.status,
                "message": result_obj.message[:500],
            },
            agents_involved=result_obj.agents_used,
            total_ai_cost=result_obj.total_ai_cost,
            duration_seconds=time.time() - start_time,
        ))
        self.audit.record(
            "journal.recorded",
            "Recorded agentic chat decision journal entry",
            detail={"status": result_obj.status},
            directive_id=directive.id,
        )
