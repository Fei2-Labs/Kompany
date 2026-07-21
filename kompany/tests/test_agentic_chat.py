"""Agentic CEO chat from the channel path (06-16-agentic-chat-engine P2).

Covers:
- a real ``answer``/``execute`` request runs the NativeRunner loop from the
  channel path (not a single ``ceo.answer()``),
- a read-only tool runs inline in chat,
- a side-effecting tool creates an inbox approval mid-chat → session goes
  ``gated`` with the approval_id (and the action does NOT run inline),
- the DirectiveResult external contract is preserved,
- streaming events are emitted to the EventHub + channel thread.
"""

from __future__ import annotations

from kompany.agents.ceo import DirectiveClassification
from kompany.core.engine import KompanyEngine
from kompany.core.agent_tools.base import FunctionTool, SideEffect
from kompany.core.agent_tools.registry import ToolRegistry
from kompany.llm.client_parts._types import LLMResponse, ToolCallRequest
from kompany.state.models import SessionStatus


# --------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------- #

class FakeLLM:
    """LLM whose ``call_with_tools`` replays a scripted list of turns.

    Each scripted turn is a list of ``ToolCallRequest`` (a tool-calling turn)
    or a final string (the model is done). ``supports_native_tools`` is True
    so the agentic path activates.
    """

    def __init__(self, engine, turns):
        self._engine = engine
        self._turns = list(turns)
        self.calls = 0

    def supports_native_tools(self, model):
        return True

    def call_with_tools(self, model, system, messages, tools, **kw):
        self.calls += 1
        self.last_system = system
        self.last_tools = tools
        turn = self._turns.pop(0) if self._turns else "Done."
        # Book a small real cost so total_ai_cost / LEDGER reflects spend.
        self._engine.cost_tracker.record(
            "fake", 100, 50, "native_runner",
        )
        if isinstance(turn, str):
            return LLMResponse(
                text=turn, input_tokens=100, output_tokens=50,
                cost_usd=0.01, model="fake", tool_calls=[],
                raw_assistant_message={"role": "assistant", "content": turn},
            )
        return LLMResponse(
            text="", input_tokens=100, output_tokens=50,
            cost_usd=0.01, model="fake", tool_calls=turn,
            raw_assistant_message={"role": "assistant", "content": [
                {"type": "tool_use", "id": c.call_id, "name": c.name,
                 "input": c.arguments} for c in turn
            ]},
        )


class FakeCEO:
    """Minimal CEO with persona methods the agentic path composes."""

    def __init__(self, classification):
        self._classification = classification

    def classify(self, raw_input, **kw):
        return self._classification

    def system_prompt(self):
        return "You are the CEO of TestCo."

    def with_founder_context(self, prompt):
        return prompt + "\n[founder-context]"


# --------------------------------------------------------------------- #
# engine builder (mirrors test_engine_channel, with agentic flag ON)
# --------------------------------------------------------------------- #

def _build_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))

    class TestSettings:
        company_name = "TestCo"
        company_goal = "AI tools"
        company_stage = "solo"
        company_time_horizon = ""
        company_exclusions = ""
        founder_profile = None
        founder_rules = None
        data_dir = tmp_path
        anthropic_api_key = "test-key"
        vault_key = ""
        currency = "EUR"
        model_apex = "claude-opus-4-20250514"
        model_primary = "claude-sonnet-4-20250514"
        model_economy = "claude-haiku-4-20250414"
        agentic_chat_enabled = True
        agentic_chat_budget_cap_usd = 0.50
        agentic_chat_max_turns = 8

        def get_model_for_tier(self, tier):
            return self.model_apex

        def get_api_key_for_provider(self, provider):
            return "test-key" if provider == "anthropic" else ""

    from kompany.agents.registry import AgentRegistry
    from kompany.llm.cost_tracker import CostTracker
    from kompany.state.agent_status import AgentStatusStore
    from kompany.state.approvals import ApprovalRequests
    from kompany.state.audit import AuditLog
    from kompany.state.conversation import ConversationStore
    from kompany.state.credentials import CredentialVaultStore
    from kompany.state.database import Database
    from kompany.state.journal import Journal
    from kompany.state.ledger import Ledger
    from kompany.state.projects import Projects
    from kompany.state.runtime import RuntimeStateStore

    settings = TestSettings()
    db = Database(tmp_path)
    ledger = Ledger(db)

    engine = KompanyEngine.__new__(KompanyEngine)
    engine.settings = settings
    engine.db = db
    engine.ledger = ledger
    engine.journal = Journal(db)
    engine.projects = Projects(db)
    engine.audit = AuditLog(db)
    engine.approvals = ApprovalRequests(db)
    engine.channel = ConversationStore(db)
    engine.agent_status = AgentStatusStore(db)
    engine.cost_tracker = CostTracker(ledger)
    engine.runtime = RuntimeStateStore(db)
    engine.credentials = CredentialVaultStore(db, settings.vault_key)
    engine.autonomy = __import__(
        "kompany.core.autonomy", fromlist=["AutonomyGate"]
    ).AutonomyGate()
    engine.registry = AgentRegistry(None, settings, ledger)
    return engine


def _install_ceo(engine, classification):
    fake = FakeCEO(classification)

    class FakeRegistry:
        def get(self, role, company_state=None):
            return fake

    engine.registry = FakeRegistry()
    return fake


def _classification(route="answer", directive_type="informational"):
    return DirectiveClassification(
        directive_type=directive_type,
        reasoning="r",
        primary_squad="strategy",
        approval_tier="auto",
        route=route,
    )


# --------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------- #

def test_agentic_session_runs_from_channel_path(tmp_path, monkeypatch):
    """A real answer request runs the loop, not a single ceo.answer call."""
    engine = _build_engine(tmp_path, monkeypatch)
    _install_ceo(engine, _classification(route="answer"))
    # Two-turn loop: no tools, the model just answers.
    engine.llm = FakeLLM(engine, ["Here is my analysis as CEO."])

    result = engine.process_directive("Research X and tell me what you think.")

    assert engine.llm.calls >= 1  # loop ran
    assert result.status == "completed"
    assert result.message == "Here is my analysis as CEO."
    # Persona composed into the system prompt.
    assert "CEO of TestCo" in engine.llm.last_system
    assert "[founder-context]" in engine.llm.last_system
    session = engine.channel.get_session(result.session_id)
    assert session.state == SessionStatus.ANSWERED
    assert result.total_ai_cost > 0  # cost surfaced


def test_read_only_tool_runs_inline(tmp_path, monkeypatch):
    """A READ tool executes inline mid-chat and feeds the loop an observation."""
    engine = _build_engine(tmp_path, monkeypatch)
    _install_ceo(engine, _classification(route="execute", directive_type="operational"))

    ran = {"count": 0}

    def _peek(args, ctx):
        ran["count"] += 1
        return "the answer is 42"

    read_tool = FunctionTool(
        name="peek", description="peek", side_effect=SideEffect.READ,
        parameters={"type": "object", "properties": {}}, func=_peek,
    )
    monkeypatch.setattr(
        "kompany.core.engine_parts.agentic_chat.build_chat_registry",
        lambda eng: ToolRegistry([read_tool]),
    )
    engine.llm = FakeLLM(engine, [
        [ToolCallRequest(call_id="c1", name="peek", arguments={})],
        "Based on the tool, the answer is 42.",
    ])

    result = engine.process_directive("Find the answer.")

    assert ran["count"] == 1  # ran inline
    assert result.status == "completed"
    assert "42" in result.message


def test_gated_tool_creates_approval_and_session_gates(tmp_path, monkeypatch):
    """A side-effecting tool proposes an approval; session goes gated."""
    engine = _build_engine(tmp_path, monkeypatch)
    _install_ceo(engine, _classification(route="execute", directive_type="operational"))

    proposed = {}

    def _propose(tool_name, inputs, summary, **kw):
        proposed["tool"] = tool_name
        return {"id": "appr-1", "tool_name": tool_name}

    engine.propose_action = _propose  # the inline gate seam

    def _must_not_run(args, ctx):
        raise AssertionError("gated tool must not run inline")

    send_tool = FunctionTool(
        name="email.send", description="send", side_effect=SideEffect.EXTERNAL_ACTION,
        parameters={"type": "object", "properties": {}}, func=_must_not_run,
    )
    monkeypatch.setattr(
        "kompany.core.engine_parts.agentic_chat.build_chat_registry",
        lambda eng: ToolRegistry([send_tool]),
    )
    engine.llm = FakeLLM(engine, [
        [ToolCallRequest(call_id="c1", name="email.send",
                         arguments={"to": "x@y.z"})],
        "It is awaiting your approval.",
    ])

    result = engine.process_directive("Email the investor.")

    assert proposed["tool"] == "email.send"
    assert result.status == "gated"
    assert result.approval_id == "appr-1"
    session = engine.channel.get_session(result.session_id)
    assert session.state == SessionStatus.GATED


def test_directive_result_contract_preserved(tmp_path, monkeypatch):
    """The board's expected /channel/send response keys are unchanged."""
    engine = _build_engine(tmp_path, monkeypatch)
    _install_ceo(engine, _classification(route="answer"))
    engine.llm = FakeLLM(engine, ["Answer."])

    result = engine.process_directive("What's the plan?")
    payload = result.to_response_dict() if hasattr(result, "to_response_dict") else None
    # to_dict shape check via model fields the board reads.
    for key in ("status", "message", "project_id", "approval_id",
                "total_ai_cost", "run_id", "session_id"):
        assert hasattr(result, key)
    assert result.run_id is not None
    assert result.session_id is not None


def test_streaming_events_emitted(tmp_path, monkeypatch):
    """Tool-call steps stream to the EventHub and the channel thread."""
    engine = _build_engine(tmp_path, monkeypatch)
    _install_ceo(engine, _classification(route="execute", directive_type="operational"))

    read_tool = FunctionTool(
        name="peek", description="peek", side_effect=SideEffect.READ,
        parameters={"type": "object", "properties": {}},
        func=lambda a, c: "obs",
    )
    monkeypatch.setattr(
        "kompany.core.engine_parts.agentic_chat.build_chat_registry",
        lambda eng: ToolRegistry([read_tool]),
    )

    published = []
    from kompany.core import event_hub
    hub = event_hub.get_event_hub()
    monkeypatch.setattr(
        hub, "publish",
        lambda et, payload=None: published.append((et, payload)),
    )

    engine.llm = FakeLLM(engine, [
        [ToolCallRequest(call_id="c1", name="peek", arguments={})],
        "Done.",
    ])
    result = engine.process_directive("Do the thing.")

    chat_events = [p for (et, p) in published
                   if et == "harness.event" and p.get("activity_kind") == "chat"]
    assert chat_events, "expected chat harness.event stream"
    kinds = {p["kind"] for p in chat_events}
    assert "tool_use" in kinds
    # Channel thread shows the agent working (progress_summary turns).
    turns = engine.channel.session_turns(result.session_id)
    assert any(t.kind == "progress_summary" for t in turns)
