"""Engine tests for the CEO-channel directive flow (06-03-ceo-channel).

Covers route detection (execute / clarify / answer), session lifecycle,
the clarify cap, closed-session errors, and backward compatibility of the
``process_directive(text)`` no-session signature for internal callers.
"""

from __future__ import annotations

import pytest

from kompany.agents.ceo import DirectiveClassification
from kompany.core.engine import KompanyEngine
from kompany.state.models import SessionStatus


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

    engine = KompanyEngine.__new__(KompanyEngine)
    engine.settings = settings
    engine.db = db
    engine.ledger = ledger
    engine.journal = Journal(db)
    engine.projects = Projects(db)
    engine.memory = AgentMemory(db)
    engine.audit = AuditLog(db)
    engine.approvals = ApprovalRequests(db)
    engine.channel = ConversationStore(db)
    engine.agent_status = AgentStatusStore(db)
    engine.checkpoints = CheckpointStore(db)
    engine.cost_tracker = CostTracker(ledger)
    engine.backups = BackupManager(tmp_path)
    engine.runtime = RuntimeStateStore(db)
    engine.remote_replay = RemoteReplayStore(db)
    engine.credentials = CredentialVaultStore(db, settings.vault_key)
    engine.tool_authorization = ToolAuthorizationStore(db)
    engine.autonomy = __import__(
        "kompany.core.autonomy", fromlist=["AutonomyGate"]
    ).AutonomyGate()
    engine.llm = None
    engine.registry = AgentRegistry(None, settings, ledger)
    engine.initialize_company(name="TestCo", goal="AI tools", capital=50.0)
    return engine


def _install_ceo(engine, classifications):
    """Install a FakeCEO that returns the given classifications in order.

    ``classifications`` is a list of DirectiveClassification objects; each
    ``classify`` call pops the next one.
    """
    queue = list(classifications)

    class FakeCEO:
        def classify(self, raw_input, directive_id=None, targets_summary=None,
                     glossary_summary=None, session_context=None,
                     clarify_capped=False):
            self.last_context = session_context
            self.last_capped = clarify_capped
            return queue.pop(0)

    fake = FakeCEO()
    original = engine.registry

    class FakeRegistry:
        def get(self, role, company_state=None):
            if role == "ceo":
                return fake
            return original.get(role, company_state)

    engine.registry = FakeRegistry()
    return fake


# ----------------------------------------------------------------------
# Backward compatibility — internal callers pass only text.
# ----------------------------------------------------------------------

def test_process_directive_no_session_opens_one(engine):
    _install_ceo(engine, [DirectiveClassification(
        directive_type="informational",
        reasoning="status",
        primary_squad="strategy",
        approval_tier="auto",
        route="answer",
    )])

    result = engine.process_directive("What's our balance?")

    assert result.status == "completed"
    assert result.session_id is not None
    session = engine.channel.get_session(result.session_id)
    assert session.state == SessionStatus.ANSWERED


# ----------------------------------------------------------------------
# Answer flow — pure question, no project created.
# ----------------------------------------------------------------------

def test_answer_flow_creates_no_project(engine):
    _install_ceo(engine, [DirectiveClassification(
        directive_type="informational",
        reasoning="balance query",
        primary_squad="strategy",
        approval_tier="auto",
        route="answer",
    )])
    before = len(engine.projects.list_active())

    result = engine.process_directive("我们现在余额多少？")

    assert result.status == "completed"
    assert result.project_id is None
    assert len(engine.projects.list_active()) == before
    session = engine.channel.get_session(result.session_id)
    assert session.state == SessionStatus.ANSWERED
    # CEO final turn recorded.
    kinds = [t.kind for t in engine.channel.session_turns(result.session_id)]
    assert kinds == ["message", "final"]


# ----------------------------------------------------------------------
# Clarify flow — ambiguous → clarify, reply converges → dispatched.
# ----------------------------------------------------------------------

def test_clarify_then_converge_to_dispatch(engine):
    fake = _install_ceo(engine, [
        DirectiveClassification(
            directive_type="operational",
            reasoning="ambiguous",
            primary_squad="strategy",
            approval_tier="auto",
            route="clarify",
            clarify_question="Which channel do you mean?",
        ),
        DirectiveClassification(
            directive_type="operational",
            reasoning="clear now",
            primary_squad="strategy",
            approval_tier="auto",
            route="execute",
        ),
    ])
    # Make the operational handler trivial (no LLM) so dispatch completes.
    engine._handle_operational = lambda d, c, ceo: __import__(
        "kompany.core.directive", fromlist=["DirectiveResult"]
    ).DirectiveResult(directive=d, status="completed", message="done",
                      agents_used=["ceo"])

    r1 = engine.process_directive("set up the thing")
    assert r1.status == "clarify"
    assert r1.message == "Which channel do you mean?"
    assert r1.session_id is not None
    session = engine.channel.get_session(r1.session_id)
    assert session.state == SessionStatus.CLARIFYING

    r2 = engine.process_directive("the email one", session_id=r1.session_id)
    assert r2.status == "completed"
    assert r2.session_id == r1.session_id
    # The clarify reply got the prior turns as session context.
    assert "Which channel do you mean?" in (fake.last_context or "")
    closed = engine.channel.get_session(r1.session_id)
    assert closed.state == SessionStatus.DISPATCHED

    turns = engine.channel.session_turns(r1.session_id)
    assert [t.kind for t in turns] == [
        "message", "clarify_question", "message", "final"
    ]


# ----------------------------------------------------------------------
# Clarify cap — at the cap a further clarify is forced to resolve.
# ----------------------------------------------------------------------

def test_clarify_cap_forces_resolution(engine):
    # Pre-fill the session to the clarify cap.
    from kompany.state.conversation import MAX_CLARIFY_TURNS
    session = engine.channel.create_session()
    for _ in range(MAX_CLARIFY_TURNS):
        engine.channel.add_turn(
            session.id, role="ceo", content="q?", kind="clarify_question"
        )
    engine.channel.update_session_state(session.id, SessionStatus.CLARIFYING)
    assert engine.channel.at_clarify_cap(session.id) is True

    # CEO tries to clarify a 6th time — engine must force execute.
    fake = _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="still ambiguous",
        primary_squad="strategy",
        approval_tier="auto",
        route="clarify",
        clarify_question="one more question?",
    )])
    engine._handle_operational = lambda d, c, ceo: __import__(
        "kompany.core.directive", fromlist=["DirectiveResult"]
    ).DirectiveResult(directive=d, status="completed", message="done",
                      agents_used=["ceo"])

    result = engine.process_directive("again", session_id=session.id)

    assert fake.last_capped is True
    assert result.status == "completed"  # forced execute, not clarify
    assert engine.channel.get_session(session.id).state == SessionStatus.DISPATCHED


# ----------------------------------------------------------------------
# Closed-session send → error.
# ----------------------------------------------------------------------

def test_closed_session_send_returns_error(engine):
    session = engine.channel.create_session()
    engine.channel.update_session_state(session.id, SessionStatus.ANSWERED)

    _install_ceo(engine, [])  # classify must never be reached
    result = engine.process_directive("hello", session_id=session.id)

    assert result.status == "failed"
    assert "closed" in result.message.lower()
    assert result.session_id == session.id


def test_unknown_session_send_returns_error(engine):
    _install_ceo(engine, [])
    result = engine.process_directive("hello", session_id="does-not-exist")
    assert result.status == "failed"
    assert result.session_id == "does-not-exist"
