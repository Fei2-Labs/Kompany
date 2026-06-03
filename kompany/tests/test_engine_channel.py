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
    return _build_engine(tmp_path, monkeypatch, initialize=True)


def _build_engine(tmp_path, monkeypatch, initialize=True):
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
    if initialize:
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


# ----------------------------------------------------------------------
# PR2 — threshold spend gate.
# ----------------------------------------------------------------------

def _trivial_operational(engine):
    """Install a no-LLM operational handler that records a run cost.

    The handler books a small ledger cost so post-GO assertions can verify
    the ACTUAL run cost (LEDGER) is recorded on the final turn.
    """
    def handler(d, c, ceo):
        from kompany.core.directive import DirectiveResult
        # Simulate real execution spend on the active run.
        engine.cost_tracker.record(
            "claude-sonnet-4-20250514", 1000, 500, "exec", directive_id=d.id
        )
        return DirectiveResult(
            directive=d,
            status="completed",
            message="done",
            total_ai_cost=engine.cost_tracker.run_total(),
            agents_used=["ceo"],
        )

    engine._handle_operational = handler


def _set_threshold(engine, value):
    engine.db.execute(
        """INSERT INTO company_config (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (engine.CHANNEL_SPEND_THRESHOLD_KEY, str(value)),
    )
    engine.db.commit()


def test_master_tier_gates_nothing_dispatched(engine):
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="irreversible",
        primary_squad="strategy",
        approval_tier="master",
        estimated_cost_eur=0.0,  # cheap, but master tier still gates
        route="execute",
        execution_plan="Delete the production database.",
    )])
    before_projects = len(engine.projects.list_active())
    before_approvals = len(engine.approvals.list_pending())

    result = engine.process_directive("nuke prod")

    assert result.status == "gated"
    assert "Delete the production database." in result.message
    assert "estimate" in result.message.lower()
    # Nothing executed: no project, no approval.
    assert len(engine.projects.list_active()) == before_projects
    assert len(engine.approvals.list_pending()) == before_approvals
    session = engine.channel.get_session(result.session_id)
    assert session.state == SessionStatus.GATED
    kinds = [t.kind for t in engine.channel.session_turns(result.session_id)]
    assert kinds == ["message", "preview"]


def test_cost_over_threshold_gates(engine):
    _set_threshold(engine, 1.0)
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="expensive",
        primary_squad="strategy",
        approval_tier="ceo",
        estimated_cost_eur=5.0,  # > 1.0 threshold
        route="execute",
    )])

    result = engine.process_directive("big job")

    assert result.status == "gated"
    assert engine.channel.get_session(result.session_id).state == SessionStatus.GATED


def test_under_threshold_executes_directly(engine):
    _set_threshold(engine, 1.0)
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="cheap",
        primary_squad="strategy",
        approval_tier="ceo",
        estimated_cost_eur=0.5,  # < 1.0 threshold, non-master
        route="execute",
    )])
    _trivial_operational(engine)

    result = engine.process_directive("small job")

    assert result.status == "completed"
    assert engine.channel.get_session(result.session_id).state == SessionStatus.DISPATCHED


def test_threshold_boundary_equal_does_not_gate(engine):
    # Boundary: estimate == threshold runs (gate is strictly ``>``).
    _set_threshold(engine, 2.0)
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="exactly at threshold",
        primary_squad="strategy",
        approval_tier="ceo",
        estimated_cost_eur=2.0,  # == threshold → runs
        route="execute",
    )])
    _trivial_operational(engine)

    result = engine.process_directive("at threshold")

    assert result.status == "completed"


def test_threshold_configurable(engine):
    # Default is 1.0; raise it to 10.0 and a 5.0 estimate now runs.
    _set_threshold(engine, 10.0)
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="now affordable",
        primary_squad="strategy",
        approval_tier="ceo",
        estimated_cost_eur=5.0,  # < 10.0 raised threshold
        route="execute",
    )])
    _trivial_operational(engine)

    result = engine.process_directive("job")

    assert result.status == "completed"


def test_default_threshold_is_one(engine):
    assert engine._channel_spend_threshold() == 1.0


def test_malformed_threshold_falls_back_to_default(engine):
    # A garbage config value must not crash the gate — it falls back to 1.0.
    engine.db.execute(
        """INSERT INTO company_config (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (engine.CHANNEL_SPEND_THRESHOLD_KEY, "not-a-number"),
    )
    engine.db.commit()
    assert engine._channel_spend_threshold() == 1.0


def test_go_dispatches_without_reclassify(engine):
    fake = _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="big",
        primary_squad="strategy",
        approval_tier="master",
        estimated_cost_eur=0.0,
        route="execute",
        execution_plan="Run the campaign.",
    )])
    # Count classify calls to assert GO does not re-classify.
    fake.classify_calls = 0
    original_classify = fake.classify

    def counting_classify(*args, **kwargs):
        fake.classify_calls += 1
        return original_classify(*args, **kwargs)

    fake.classify = counting_classify
    _trivial_operational(engine)

    gated = engine.process_directive("launch")
    assert gated.status == "gated"
    assert fake.classify_calls == 1

    result = engine.channel_go(gated.session_id)

    assert result.status == "completed"
    assert result.session_id == gated.session_id
    # No second classify — the held snapshot was replayed.
    assert fake.classify_calls == 1
    session = engine.channel.get_session(gated.session_id)
    assert session.state == SessionStatus.DISPATCHED
    # The final turn records the ACTUAL run cost, > 0 from execution.
    turns = engine.channel.session_turns(gated.session_id)
    assert [t.kind for t in turns] == ["message", "preview", "final"]
    final = turns[-1]
    assert final.cost > 0.0


def test_go_on_non_gated_session_errors(engine):
    # Answered (terminal) session — GO is illegal.
    session = engine.channel.create_session()
    engine.channel.update_session_state(session.id, SessionStatus.ANSWERED)

    result = engine.channel_go(session.id)

    assert result.status == "failed"
    assert result.session_id == session.id
    # Session unchanged.
    assert engine.channel.get_session(session.id).state == SessionStatus.ANSWERED


def test_go_on_unknown_session_errors(engine):
    result = engine.channel_go("nope")
    assert result.status == "failed"


def test_abandon_from_gated(engine):
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="big",
        primary_squad="strategy",
        approval_tier="master",
        estimated_cost_eur=0.0,
        route="execute",
        execution_plan="Spend big.",
    )])
    gated = engine.process_directive("spend")
    assert gated.status == "gated"

    result = engine.channel_abandon(gated.session_id)

    assert result.status == "abandoned"
    session = engine.channel.get_session(gated.session_id)
    assert session.state == SessionStatus.ABANDONED
    kinds = [t.kind for t in engine.channel.session_turns(gated.session_id)]
    assert kinds == ["message", "preview", "final"]
    # A subsequent GO on the abandoned session must fail (no replay).
    after = engine.channel_go(gated.session_id)
    assert after.status == "failed"


def test_abandon_from_clarifying(engine):
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="ambiguous",
        primary_squad="strategy",
        approval_tier="auto",
        route="clarify",
        clarify_question="Which one?",
    )])
    clar = engine.process_directive("do the thing")
    assert clar.status == "clarify"

    result = engine.channel_abandon(clar.session_id)

    assert result.status == "abandoned"
    session = engine.channel.get_session(clar.session_id)
    assert session.state == SessionStatus.ABANDONED
    kinds = [t.kind for t in engine.channel.session_turns(clar.session_id)]
    assert kinds == ["message", "clarify_question", "final"]


def test_abandon_on_closed_session_errors(engine):
    session = engine.channel.create_session()
    engine.channel.update_session_state(session.id, SessionStatus.DISPATCHED)
    result = engine.channel_abandon(session.id)
    assert result.status == "failed"


def test_preview_labels_estimate_and_tier(engine):
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="r",
        primary_squad="strategy",
        approval_tier="master",
        estimated_cost_eur=3.5,
        route="execute",
        execution_plan="Big plan.",
    )])
    result = engine.process_directive("go big")
    assert "€3.50" in result.message
    assert "master" in result.message.lower()
    assert "estimate" in result.message.lower()


# ----------------------------------------------------------------------
# Restart survival — a gated session is a parked founder decision; the
# desktop app restarts the engine routinely. GO must work against a brand
# new engine instance (in-memory cache gone) by rehydrating the snapshot
# from the persisted session row — WITHOUT re-classifying.
# ----------------------------------------------------------------------

def test_go_survives_engine_restart(tmp_path, monkeypatch):
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="big",
        primary_squad="strategy",
        approval_tier="master",
        estimated_cost_eur=0.0,
        route="execute",
        execution_plan="Run the campaign.",
    )])
    _trivial_operational(engine)

    gated = engine.process_directive("launch")
    assert gated.status == "gated"
    session_id = gated.session_id

    # The snapshot is persisted on the session row (survives a restart).
    persisted = engine.channel.get_session(session_id)
    assert persisted.payload.get("gated_directive")

    # Simulate an engine restart: a brand-new engine instance on the SAME
    # data dir. Its in-memory _gated_directives cache is empty.
    restarted = _build_engine(tmp_path, monkeypatch, initialize=False)
    assert not getattr(restarted, "_gated_directives", {})
    # Re-classify would fail loudly (no CEO installed on the new instance);
    # if GO tried to classify this would raise — proving no re-classify.
    _trivial_operational(restarted)

    result = restarted.channel_go(session_id)

    assert result.status == "completed"
    assert result.session_id == session_id
    restarted_session = restarted.channel.get_session(session_id)
    assert restarted_session.state == SessionStatus.DISPATCHED
    # Durable snapshot cleaned up after a successful GO.
    assert "gated_directive" not in (restarted_session.payload or {})
    turns = restarted.channel.session_turns(session_id)
    assert [t.kind for t in turns] == ["message", "preview", "final"]
    assert turns[-1].cost > 0.0


def test_abandon_after_restart_clears_persisted_snapshot(tmp_path, monkeypatch):
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="big",
        primary_squad="strategy",
        approval_tier="master",
        estimated_cost_eur=0.0,
        route="execute",
        execution_plan="Spend big.",
    )])
    gated = engine.process_directive("spend")
    session_id = gated.session_id

    restarted = _build_engine(tmp_path, monkeypatch, initialize=False)
    result = restarted.channel_abandon(session_id)

    assert result.status == "abandoned"
    session = restarted.channel.get_session(session_id)
    assert session.state == SessionStatus.ABANDONED
    assert "gated_directive" not in (session.payload or {})
    # A subsequent GO on the abandoned session must fail (no replay).
    assert restarted.channel_go(session_id).status == "failed"


# ----------------------------------------------------------------------
# Mid-GO dispatch failure — the session must stay GATED (retryable), no
# exception leaks, and the snapshot is preserved for a retry.
# ----------------------------------------------------------------------

def test_go_dispatch_failure_keeps_session_gated(engine):
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="big",
        primary_squad="strategy",
        approval_tier="master",
        estimated_cost_eur=0.0,
        route="execute",
        execution_plan="Run it.",
    )])

    def boom(d, c, ceo):
        raise RuntimeError("dispatch exploded")

    gated = engine.process_directive("launch")
    assert gated.status == "gated"

    engine._handle_operational = boom
    result = engine.channel_go(gated.session_id)

    # Clean failed result — no exception leaked.
    assert result.status == "failed"
    assert "failed after GO" in result.message.lower() or "failed" in result.message.lower()
    # Session stays gated for retry.
    session = engine.channel.get_session(gated.session_id)
    assert session.state == SessionStatus.GATED
    # Snapshot preserved (durable + cache) so a retry works.
    assert session.payload.get("gated_directive")

    # Retry with a working handler succeeds.
    _trivial_operational(engine)
    retry = engine.channel_go(gated.session_id)
    assert retry.status == "completed"
    assert engine.channel.get_session(gated.session_id).state == SessionStatus.DISPATCHED
