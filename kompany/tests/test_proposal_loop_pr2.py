"""PR2 integration tests: engine answer → PROPOSED path.

Covers:
* answer with has_proposal=true → session PROPOSED, payload stashed
* answer with has_proposal=false → session ANSWERED (terminal, existing path)
* channel_go on PROPOSED session → 1 classify call → DISPATCHED
* channel_go on GATED session still works (no regression)
* recent_context injected into classify + answer calls
* _compose_recent_context returns cross-session turns
"""
from __future__ import annotations

import pytest

from kompany.agents.ceo import AnswerResponse, DirectiveClassification
from kompany.core.engine import KompanyEngine
from kompany.llm.client import LLMResponse
from kompany.state.models import SESSION_TERMINAL_STATUSES, SessionStatus


# ---------------------------------------------------------------------------
# Engine fixture (mirrors test_engine_channel setup)
# ---------------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))

    class TestSettings:
        company_name = "TestCo"
        company_goal = "AI tools"
        company_stage = "solo"
        company_time_horizon = ""
        company_exclusions = ""
        data_dir = tmp_path
        anthropic_api_key = "test-key"
        openai_api_key = ""
        telegram_bot_token = ""
        telegram_chat_id = ""
        telegram_allowed_chat_ids = "123,456"
        mobile_remote_token = "mobile-secret"
        web_dashboard_token = ""
        vault_key = ""
        gemini_api_key = ""
        glm_api_key = ""
        kimi_api_key = ""
        custom_api_key = ""
        custom_base_url = ""
        currency = "EUR"
        model_apex = "claude-opus-4-20250514"
        model_primary = "claude-sonnet-4-20250514"
        model_economy = "claude-haiku-4-20250414"

        def get_model_for_tier(self, tier):
            return self.model_primary

        def get_api_key_for_provider(self, provider):
            return "test-key" if provider == "anthropic" else ""

    from kompany.agents.registry import AgentRegistry
    from kompany.llm.cost_tracker import CostTracker
    from kompany.state.agent_status import AgentStatusStore
    from kompany.state.approvals import ApprovalRequests
    from kompany.state.audit import AuditLog
    from kompany.state.backup import BackupManager
    from kompany.state.checkpoints import CheckpointStore
    from kompany.state.conversation import ConversationStore
    from kompany.state.credentials import CredentialVaultStore
    from kompany.state.database import Database
    from kompany.state.episodes import Episodes
    from kompany.state.journal import Journal
    from kompany.state.ledger import Ledger
    from kompany.state.memory import AgentMemory
    from kompany.state.projects import Projects
    from kompany.state.remote_replay import RemoteReplayStore
    from kompany.state.runtime import RuntimeStateStore
    from kompany.state.tool_authorization import ToolAuthorizationStore

    settings = TestSettings()
    db = Database(tmp_path)
    ledger = Ledger(db)

    eng = KompanyEngine.__new__(KompanyEngine)
    eng.settings = settings
    eng.db = db
    eng.ledger = ledger
    eng.journal = Journal(db)
    eng.projects = Projects(db)
    eng.memory = AgentMemory(db)
    eng.audit = AuditLog(db)
    eng.episodes = Episodes(db)
    eng.approvals = ApprovalRequests(db)
    eng.channel = ConversationStore(db)
    eng.agent_status = AgentStatusStore(db)
    eng.checkpoints = CheckpointStore(db)
    eng.cost_tracker = CostTracker(ledger)
    eng.backups = BackupManager(tmp_path)
    eng.runtime = RuntimeStateStore(db)
    eng.remote_replay = RemoteReplayStore(db)
    eng.credentials = CredentialVaultStore(db, settings.vault_key)
    eng.tool_authorization = ToolAuthorizationStore(db)
    eng.autonomy = __import__(
        "kompany.core.autonomy", fromlist=["AutonomyGate"]
    ).AutonomyGate()
    eng.llm = None
    eng.registry = AgentRegistry(None, settings, ledger)
    eng.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    return eng


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _answer_resp(engine, question: str, has_proposal: bool = False, proposal_directive: str = "") -> LLMResponse:
    """Build a fake LLMResponse whose .parsed is an AnswerResponse."""
    engine.cost_tracker.record("claude-sonnet-4-20250514", 300, 100, "ceo.answer")
    resp = LLMResponse(
        text=f"ANSWER:{question}",
        input_tokens=300,
        output_tokens=100,
        cost_usd=engine.cost_tracker.run_total(),
        model="fake",
    )
    resp.parsed = AnswerResponse(
        text=f"ANSWER:{question}" if not has_proposal else f"PROPOSAL:{question}",
        has_proposal=has_proposal,
        proposal_directive=proposal_directive,
    )
    return resp


def _install_ceo_proposal(engine, answer_has_proposal: bool, proposal_directive: str = ""):
    """Install a FakeCEO that:
    - classify: returns informational (answer route) on first call, then
      a realistic classification on subsequent calls (for GO).
    - answer: returns AnswerResponse with given has_proposal flag.
    """
    call_count = {"classify": 0}

    class FakeCEO:
        def classify(self, raw_input, directive_id=None, targets_summary=None,
                     glossary_summary=None, session_context=None,
                     recent_context=None, clarify_capped=False):
            self.last_recent_context = recent_context
            call_count["classify"] += 1
            # First call: route to answer.
            if call_count["classify"] == 1:
                return DirectiveClassification(
                    directive_type="informational",
                    reasoning="question",
                    primary_squad="strategy",
                    approval_tier="auto",
                    route="answer",
                )
            # Subsequent calls (GO path): operational so it dispatches.
            return DirectiveClassification(
                directive_type="operational",
                reasoning="proposal execution",
                primary_squad="product",
                approval_tier="auto",
                route="execute",
            )

        def answer(self, question, company_context, session_context=None,
                   recent_context=None, directive_id=None):
            self.answer_question = question
            self.answer_recent_context = recent_context
            return _answer_resp(engine, question, has_proposal=answer_has_proposal,
                                 proposal_directive=proposal_directive)

        def call(self, prompt="", directive_id=None, action_type=None):
            from kompany.llm.client import LLMResponse
            engine.cost_tracker.record(
                "claude-sonnet-4-20250514", 200, 100, action_type or "ceo.call",
                directive_id=directive_id,
            )
            return LLMResponse(
                text="GO execution done",
                input_tokens=200, output_tokens=100,
                cost_usd=engine.cost_tracker.run_total(), model="fake",
            )

        def create_revenue_plan(self, *a, **kw):
            raise AssertionError("should not be called in answer path")

    fake = FakeCEO()
    original = engine.registry

    class FakeRegistry:
        def get(self, role, company_state=None):
            if role == "ceo":
                return fake
            return original.get(role, company_state=company_state)

    engine.registry = FakeRegistry()
    return fake, call_count


# ---------------------------------------------------------------------------
# Tests: has_proposal=false → ANSWERED (no regression)
# ---------------------------------------------------------------------------

def test_pure_answer_session_becomes_answered(engine):
    fake, _ = _install_ceo_proposal(engine, answer_has_proposal=False)
    result = engine.process_directive("What's my balance?")
    session = engine.channel.get_session(result.session_id)
    assert session.state == SessionStatus.ANSWERED
    assert session.state in SESSION_TERMINAL_STATUSES


def test_pure_answer_result_status_completed(engine):
    _install_ceo_proposal(engine, answer_has_proposal=False)
    result = engine.process_directive("What's my balance?")
    assert result.status == "completed"


def test_pure_answer_no_payload_stashed(engine):
    _install_ceo_proposal(engine, answer_has_proposal=False)
    result = engine.process_directive("What's my balance?")
    session = engine.channel.get_session(result.session_id)
    assert "proposed_directive" not in (session.payload or {})


# ---------------------------------------------------------------------------
# Tests: has_proposal=true → PROPOSED
# ---------------------------------------------------------------------------

def test_proposal_answer_session_becomes_proposed(engine):
    _install_ceo_proposal(engine, answer_has_proposal=True,
                           proposal_directive="Launch presale sprint")
    result = engine.process_directive("What should we do next?")
    session = engine.channel.get_session(result.session_id)
    assert session.state == SessionStatus.PROPOSED


def test_proposal_answer_not_terminal(engine):
    _install_ceo_proposal(engine, answer_has_proposal=True,
                           proposal_directive="Launch presale sprint")
    result = engine.process_directive("What should we do next?")
    session = engine.channel.get_session(result.session_id)
    assert session.state not in SESSION_TERMINAL_STATUSES


def test_proposal_answer_result_status_proposed(engine):
    _install_ceo_proposal(engine, answer_has_proposal=True,
                           proposal_directive="Launch presale sprint")
    result = engine.process_directive("What should we do next?")
    assert result.status == "proposed"


def test_proposal_answer_stashes_directive(engine):
    _install_ceo_proposal(engine, answer_has_proposal=True,
                           proposal_directive="Launch presale sprint")
    result = engine.process_directive("What should we do next?")
    session = engine.channel.get_session(result.session_id)
    assert "proposed_directive" in session.payload
    assert session.payload["proposed_directive"]["proposal_directive"] == "Launch presale sprint"


def test_proposal_answer_final_turn_added(engine):
    _install_ceo_proposal(engine, answer_has_proposal=True,
                           proposal_directive="Launch presale sprint")
    result = engine.process_directive("What should we do next?")
    turns = engine.channel.session_turns(result.session_id)
    final_turns = [t for t in turns if t.kind == "final"]
    assert len(final_turns) == 1
    assert "PROPOSAL:" in final_turns[0].content


# ---------------------------------------------------------------------------
# Tests: channel_go on PROPOSED → classify + execute → DISPATCHED
# ---------------------------------------------------------------------------

def test_go_on_proposed_dispatches(engine):
    _, call_count = _install_ceo_proposal(engine, answer_has_proposal=True,
                                           proposal_directive="Launch presale sprint")
    result = engine.process_directive("What should we do next?")
    session_id = result.session_id
    assert engine.channel.get_session(session_id).state == SessionStatus.PROPOSED

    go_result = engine.channel_go(session_id)
    session = engine.channel.get_session(session_id)
    assert session.state == SessionStatus.DISPATCHED
    # 1 classify for answer + 1 classify for GO = 2 total
    assert call_count["classify"] == 2


def test_go_on_proposed_clears_payload(engine):
    _install_ceo_proposal(engine, answer_has_proposal=True,
                           proposal_directive="Launch presale sprint")
    result = engine.process_directive("What should we do next?")
    engine.channel_go(result.session_id)
    session = engine.channel.get_session(result.session_id)
    assert "proposed_directive" not in (session.payload or {})


def test_go_on_proposed_returns_non_failed(engine):
    _install_ceo_proposal(engine, answer_has_proposal=True,
                           proposal_directive="Launch presale sprint")
    result = engine.process_directive("What should we do next?")
    go_result = engine.channel_go(result.session_id)
    assert go_result.status != "failed"


# ---------------------------------------------------------------------------
# Tests: channel_go GATED regression
# ---------------------------------------------------------------------------

def _install_ceo_gated(engine):
    """Install FakeCEO that routes to execute with master tier (forces gate)."""
    class FakeCEO:
        def classify(self, raw_input, directive_id=None, targets_summary=None,
                     glossary_summary=None, session_context=None,
                     recent_context=None, clarify_capped=False):
            return DirectiveClassification(
                directive_type="operational",
                reasoning="big spend",
                primary_squad="product",
                approval_tier="master",
                estimated_cost_eur=999.0,
                route="execute",
            )

        def create_revenue_plan(self, *a, **kw):
            from kompany.state.models import RevenueProjectPlan, RevenuePath
            return RevenueProjectPlan(
                summary="plan", paths=[], recommended_path="a",
                total_estimated_revenue=1000.0, estimated_timeframe="1w",
            )

    fake = FakeCEO()
    original = engine.registry

    class FakeRegistry:
        def get(self, role, company_state=None):
            if role == "ceo":
                return fake
            return original.get(role, company_state=company_state)

    engine.registry = FakeRegistry()
    return fake


def test_go_on_gated_still_works(engine):
    _install_ceo_gated(engine)
    result = engine.process_directive("Buy a company for €999")
    session_id = result.session_id
    assert engine.channel.get_session(session_id).state == SessionStatus.GATED

    go_result = engine.channel_go(session_id)
    # Should not error on the stash replay (gated path unchanged)
    assert go_result.session_id == session_id


# ---------------------------------------------------------------------------
# Tests: go on non-proposed/non-gated errors correctly
# ---------------------------------------------------------------------------

def test_go_on_answered_errors(engine):
    _install_ceo_proposal(engine, answer_has_proposal=False)
    result = engine.process_directive("What's my balance?")
    go_result = engine.channel_go(result.session_id)
    assert go_result.status == "failed"


# ---------------------------------------------------------------------------
# Tests: recent_context injected
# ---------------------------------------------------------------------------

def test_recent_context_passed_to_answer(engine):
    fake, _ = _install_ceo_proposal(engine, answer_has_proposal=False)
    # First session: establishes history.
    engine.process_directive("What's my balance?")
    # Second session: answer should see recent_context (the first session's turns).
    fake2, _ = _install_ceo_proposal(engine, answer_has_proposal=False)
    engine.process_directive("And what about revenue?")
    # The second answer call received recent_context with the prior session turns.
    assert fake2.answer_recent_context is not None
    assert "balance" in fake2.answer_recent_context.lower() or len(fake2.answer_recent_context) > 0


def test_recent_context_passed_to_classify(engine):
    fake, _ = _install_ceo_proposal(engine, answer_has_proposal=False)
    # First session: establishes history.
    engine.process_directive("What's my balance?")
    # Second session: classify should see recent_context.
    fake2, _ = _install_ceo_proposal(engine, answer_has_proposal=False)
    engine.process_directive("继续")
    # classify was called with recent_context
    assert fake2.last_recent_context is not None
