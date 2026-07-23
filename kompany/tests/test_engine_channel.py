"""Engine tests for the CEO-channel directive flow (06-03-ceo-channel).

Covers route detection (execute / clarify / answer), session lifecycle,
the clarify cap, closed-session errors, and backward compatibility of the
``process_directive(text)`` no-session signature for internal callers.
"""

from __future__ import annotations

import json

import pytest

from kompany.agents.ceo import DirectiveClassification
from kompany.channels.context import DirectiveContext
from kompany.core.engine import KompanyEngine
from kompany.state.models import Project, ProjectStatus, ProjectType, SessionStatus
from kompany.state.targets import CompanyTargets


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
    from kompany.state.delegations import DelegationStore
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

    engine = KompanyEngine.__new__(KompanyEngine)
    engine.settings = settings
    engine.db = db
    engine.ledger = ledger
    engine.journal = Journal(db)
    engine.projects = Projects(db)
    engine.delegations = DelegationStore(db, engine.projects)
    engine.memory = AgentMemory(db)
    engine.audit = AuditLog(db)
    engine.episodes = Episodes(db)
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
                     recent_context=None, clarify_capped=False):
            self.last_context = session_context
            self.last_recent_context = recent_context
            self.last_capped = clarify_capped
            return queue.pop(0)

        def answer(self, question, company_context, session_context=None,
                   recent_context=None, directive_id=None):
            # Echo a marker incorporating the founder's actual question so a
            # test can prove the reply is question-driven (not a canned
            # template). Capture the context the engine assembled so a test
            # can assert it carries real project/task + staff sections.
            self.answer_question = question
            self.answer_company_context = company_context
            self.answer_session_context = session_context
            self.answer_recent_context = recent_context
            # Book a real run cost so the engine's run_total() (LEDGER) reflects
            # the spend, mirroring how base.call would record via the tracker.
            engine.cost_tracker.record(
                "claude-sonnet-4-20250514", 800, 200, "ceo.answer",
                directive_id=directive_id,
            )
            from kompany.agents.ceo import AnswerResponse
            from kompany.llm.client import LLMResponse
            resp = LLMResponse(
                text=f"ANSWER_MARKER :: {question}",
                input_tokens=800,
                output_tokens=200,
                cost_usd=engine.cost_tracker.run_total(),
                model="fake",
            )
            resp.parsed = AnswerResponse(
                text=f"ANSWER_MARKER :: {question}",
                has_proposal=False,
                proposal_directive="",
            )
            return resp

    fake = FakeCEO()
    original = engine.registry

    class FakeRegistry:
        def get(self, role, company_state=None):
            if role == "ceo":
                return fake
            return original.get(role, company_state)

        def descriptor(self, role):
            return original.descriptor(role)

    engine.registry = FakeRegistry()
    return fake


# ----------------------------------------------------------------------
# Backward compatibility — internal callers pass only text.
# ----------------------------------------------------------------------

def test_process_directive_no_session_opens_one(engine):
    fake = _install_ceo(engine, [DirectiveClassification(
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
    # The reply is CEO-generated and driven by the founder's actual question.
    assert result.message == "ANSWER_MARKER :: What's our balance?"
    assert fake.answer_question == "What's our balance?"


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


def test_answer_is_question_driven_not_canned(engine):
    """PR8: answer context carries active work, recent completion, and targets.

    Asserts (1) the reply is generated from the founder's question, (2) the
    old canned company-financials template is NOT what gets returned, (3) the
    context handed to CEO.answer() distinguishes active work now vs recent
    completed work vs current mission/targets, and (4) the real run cost +
    agents_used are recorded.
    """
    # Seed real state so the assembled context has projects/tasks + staff.
    from kompany.state.models import Project, Task, TaskStatus

    active = Project(
        name="Launch landing page", type="operational",
        status=ProjectStatus.ACTIVE, target_amount=100.0, funded_amount=10.0,
        assigned_agents=["cmo"],
    )
    engine.projects.create(active)
    engine.projects.create_task(Task(
        project_id=active.id, title="Draft hero copy",
        status=TaskStatus.ACTIVE, assigned_agent="cmo",
    ))
    engine.agent_status.set("cmo", "working", "Draft hero copy")

    done = Project(
        name="Shipped onboarding refresh", type="operational",
        status=ProjectStatus.COMPLETED, target_amount=0.0, funded_amount=0.0,
        assigned_agents=["ceo"],
    )
    engine.projects.create(done)
    engine.episodes.record_or_update(done.id)
    engine.set_targets(CompanyTargets(
        initial_budget=50.0,
        revenue_target=1000.0,
        customer_target=10,
        deadline="2026-08-31T00:00:00+00:00",
        source="agreed",
    ))

    fake = _install_ceo(engine, [DirectiveClassification(
        directive_type="informational",
        reasoning="team status query",
        primary_squad="strategy",
        approval_tier="auto",
        route="answer",
    )])

    question = "现在团队正在进行的任务有哪些"
    result = engine.process_directive(question)

    # (1) reply is generated from the founder's actual question.
    assert result.message == f"ANSWER_MARKER :: {question}"
    assert fake.answer_question == question

    # (2) the old canned financials template is NOT returned.
    assert "Total income:" not in result.message
    assert "Total AI costs:" not in result.message

    # (3) the context passed to answer() carries the new, separated sections.
    ctx = fake.answer_company_context
    assert "MISSION / TARGETS CURRENTLY SET:" in ctx
    assert "Status: set (agreed)" in ctx
    assert "Company targets:" in ctx
    assert "/ui/onboarding.html" in ctx
    assert "ACTIVE WORK NOW:" in ctx
    assert "Active projects: 1" in ctx
    assert "Open tasks in active projects: 1" in ctx
    assert "Launch landing page" in ctx
    assert "Draft hero copy" in ctx
    assert "RECENT COMPLETED WORK:" in ctx
    assert "Completed episodes/projects shown: 1" in ctx
    assert "Shipped onboarding refresh" in ctx
    assert "STAFF ACTIVITY:" in ctx
    assert "cmo" in ctx
    assert "FINANCIALS:" in ctx

    # (4) real cost recorded + agents_used reflects the CEO (and CFO summary).
    assert result.total_ai_cost > 0
    assert result.agents_used == ["ceo", "cfo"]
    # The persisted final turn carries the same real cost.
    final = engine.channel.session_turns(result.session_id)[-1]
    assert final.kind == "final"
    assert final.cost == result.total_ai_cost


# ----------------------------------------------------------------------
# Clarify flow — ambiguous → clarify, reply converges → dispatched.
# ----------------------------------------------------------------------

def test_answer_context_surfaces_completed_work_when_no_active_projects(engine):
    """PR8: zero active work must still surface recent completed work + targets."""
    from kompany.state.models import Project

    done1 = Project(
        name="Closed first sales sprint", type="strategic",
        status=ProjectStatus.COMPLETED, target_amount=0.0, funded_amount=0.0,
        assigned_agents=["cro"],
    )
    done2 = Project(
        name="Published pricing page", type="operational",
        status=ProjectStatus.COMPLETED, target_amount=0.0, funded_amount=0.0,
        assigned_agents=["cmo"],
    )
    engine.projects.create(done1)
    engine.projects.create(done2)
    engine.episodes.record_or_update(done1.id)
    engine.episodes.record_or_update(done2.id)
    engine.set_targets(CompanyTargets(
        initial_budget=50.0,
        revenue_target=3000.0,
        customer_target=20,
        deadline="2026-09-01T00:00:00+00:00",
        source="agreed",
    ))

    fake = _install_ceo(engine, [DirectiveClassification(
        directive_type="informational",
        reasoning="team status query",
        primary_squad="strategy",
        approval_tier="auto",
        route="answer",
    )])

    result = engine.process_directive("现在团队正在进行的任务有哪些")

    assert result.status == "completed"
    ctx = fake.answer_company_context
    assert "ACTIVE WORK NOW:" in ctx
    assert "Active projects: 0" in ctx
    assert "Open tasks in active projects: 0" in ctx
    assert "(none right now)" in ctx
    assert "RECENT COMPLETED WORK:" in ctx
    assert "Completed episodes/projects shown: 2" in ctx
    assert "Closed first sales sprint" in ctx or "Published pricing page" in ctx
    assert "MISSION / TARGETS CURRENTLY SET:" in ctx
    assert "Status: set (agreed)" in ctx
    assert "/ui/onboarding.html" in ctx


def test_answer_context_reads_recent_completed_work_with_limit(engine, monkeypatch):
    """PR8: completed-work lookup stays bounded at the store layer."""
    calls: list[tuple[str | None, int | None]] = []
    original = engine.episodes.list

    def wrapped(retention_tier=None, *, limit=None):
        calls.append((retention_tier, limit))
        return original(retention_tier, limit=limit)

    monkeypatch.setattr(engine.episodes, "list", wrapped)

    fake = _install_ceo(engine, [DirectiveClassification(
        directive_type="informational",
        reasoning="team status query",
        primary_squad="strategy",
        approval_tier="auto",
        route="answer",
    )])

    result = engine.process_directive("现在团队正在进行的任务有哪些")

    assert result.status == "completed"
    assert calls == [(None, 4)]
    assert "RECENT COMPLETED WORK:" in fake.answer_company_context


def test_directive_context_isolates_recent_history_by_project(engine):
    classification = DirectiveClassification(
        directive_type="informational",
        reasoning="project question",
        primary_squad="strategy",
        approval_tier="auto",
        route="answer",
    )
    fake = _install_ceo(engine, [classification, classification, classification])

    project_a = DirectiveContext(
        channel="board",
        account_id="founder",
        chat_id="main",
        sender_id="founder",
        project_id="project-a",
    )
    project_b = DirectiveContext(
        channel="board",
        account_id="founder",
        chat_id="main",
        sender_id="founder",
        project_id="project-b",
    )

    engine.process_directive("Project A private detail", context=project_a)
    engine.process_directive("Project B starting point", context=project_b)
    result = engine.process_directive("Continue this project", context=project_b)

    assert result.status == "completed"
    assert "Project B starting point" in (fake.answer_recent_context or "")
    assert "Project A private detail" not in (fake.answer_recent_context or "")

    session = engine.channel.get_session(result.session_id)
    assert session.project_id == "project-b"
    assert session.channel == "board"
    assert session.active_agent_id == "ceo"


def test_directive_context_isolates_same_project_id_between_companies(engine):
    classification = DirectiveClassification(
        directive_type="informational",
        reasoning="project question",
        primary_squad="strategy",
        approval_tier="auto",
        route="answer",
    )
    fake = _install_ceo(engine, [classification, classification, classification])
    company_a = DirectiveContext(
        company_id="company-a",
        channel="board",
        account_id="founder",
        chat_id="main",
        sender_id="founder",
        project_id="shared-project-id",
    )
    company_b = DirectiveContext(
        company_id="company-b",
        channel="board",
        account_id="founder",
        chat_id="main",
        sender_id="founder",
        project_id="shared-project-id",
    )

    engine.process_directive("Company A private detail", context=company_a)
    engine.process_directive("Company B starting point", context=company_b)
    result = engine.process_directive("Continue this project", context=company_b)

    assert result.status == "completed"
    assert "Company B starting point" in (fake.answer_recent_context or "")
    assert "Company A private detail" not in (fake.answer_recent_context or "")


def test_no_project_lobby_does_not_receive_project_history(engine):
    classification = DirectiveClassification(
        directive_type="informational",
        reasoning="question",
        primary_squad="strategy",
        approval_tier="auto",
        route="answer",
    )
    fake = _install_ceo(engine, [classification, classification])
    project = DirectiveContext(
        channel="board",
        account_id="local",
        chat_id="main",
        sender_id="founder",
        project_id="private-project",
    )
    lobby = DirectiveContext(
        channel="board",
        account_id="local",
        chat_id="main",
        sender_id="founder",
    )

    engine.process_directive("Private project detail", context=project)
    result = engine.process_directive("What should I do next?", context=lobby)

    assert result.status == "completed"
    assert "Private project detail" not in (fake.answer_recent_context or "")


def test_session_cannot_continue_under_a_different_project_context(engine):
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="needs detail",
        primary_squad="strategy",
        approval_tier="auto",
        route="clarify",
        clarify_question="Which deliverable?",
    )])
    project_a = DirectiveContext(
        channel="board",
        account_id="founder",
        chat_id="main",
        sender_id="founder",
        project_id="project-a",
    )
    project_b = DirectiveContext(
        channel="board",
        account_id="founder",
        chat_id="main",
        sender_id="founder",
        project_id="project-b",
    )

    first = engine.process_directive("Continue the launch", context=project_a)
    result = engine.process_directive(
        "The pricing page",
        session_id=first.session_id,
        context=project_b,
    )

    assert result.status == "failed"
    assert "does not match" in result.message
    assert len(engine.channel.session_turns(first.session_id)) == 2


def test_legacy_session_binds_transport_identity_on_first_continuation(engine):
    session = engine.channel.create_session()
    context = DirectiveContext(
        channel="board",
        account_id="local",
        chat_id="main",
        sender_id="founder",
    )

    result = engine.process_directive(
        "/status",
        session_id=session.id,
        context=context,
    )

    assert result.status == "completed"
    bound = engine.channel.get_session(session.id)
    assert bound.channel == "board"
    assert bound.account_id == "local"
    assert bound.chat_id == "main"
    assert bound.sender_id == "founder"


def test_directive_records_project_and_agent_shadow_route_decisions(engine):
    engine.projects.create(
        Project(
            id="vinted",
            name="Vinted",
            type=ProjectType.REVENUE,
        )
    )
    _install_ceo(engine, [DirectiveClassification(
        directive_type="informational",
        reasoning="campaign review",
        primary_squad="growth",
        agents_needed=["cmo"],
        approval_tier="ceo",
        route="answer",
    )])
    context = DirectiveContext(
        channel="board",
        account_id="local",
        chat_id="main",
        sender_id="founder",
        project_id="vinted",
    )

    result = engine.process_directive(
        "Review the Vinted campaign",
        context=context,
    )

    assert result.status == "completed"
    events = {row["event_type"]: row for row in engine.audit.recent(10)}
    project_detail = json.loads(
        events["routing.project.shadow"]["detail"]
    )
    agent_detail = json.loads(
        events["routing.agent.shadow"]["detail"]
    )
    assert project_detail["status"] == "resolved"
    assert project_detail["project_id"] == "vinted"
    assert agent_detail["action"] == "handoff"
    assert agent_detail["destination_agent_ids"] == ["cmo"]


def test_multi_agent_request_creates_durable_ceo_owned_delegation(engine):
    engine.projects.create(
        Project(
            id="vinted",
            name="Vinted",
            type=ProjectType.REVENUE,
        )
    )
    _install_ceo(engine, [DirectiveClassification(
        directive_type="informational",
        reasoning="needs coordinated growth and finance review",
        primary_squad="strategy",
        agents_needed=["cmo", "cfo"],
        approval_tier="auto",
        estimated_cost_eur=0.4,
        route="answer",
    )])
    context = DirectiveContext(
        channel="board",
        account_id="local",
        chat_id="main",
        sender_id="founder",
        project_id="vinted",
    )

    result = engine.process_directive(
        "Review campaign performance and budget",
        context=context,
    )

    assert result.status == "delegated"
    assert result.delegation_id
    assert result.active_agent_id == "ceo"
    assert result.conversation_continues is True
    delegation = engine.get_delegation(result.delegation_id)
    assert delegation.project_id == "vinted"
    assert delegation.parent_agent_id == "ceo"
    assert delegation.parent_run_id == result.run_id
    assert [child.assigned_agent for child in delegation.children] == [
        "cmo",
        "cfo",
    ]
    assert delegation.context_packet["user_intent"] == (
        "Review campaign performance and budget"
    )


def test_ceo_can_cancel_delegation_without_losing_conversation_ownership(engine):
    engine.projects.create(Project(
        id="vinted",
        name="Vinted",
        type=ProjectType.REVENUE,
    ))
    _install_ceo(engine, [DirectiveClassification(
        directive_type="informational",
        reasoning="needs coordinated review",
        primary_squad="strategy",
        agents_needed=["cmo", "cfo"],
        approval_tier="auto",
        route="answer",
    )])
    context = DirectiveContext(
        channel="board",
        account_id="local",
        chat_id="main",
        sender_id="founder",
        project_id="vinted",
    )
    created = engine.process_directive(
        "Review campaign performance and budget",
        context=context,
    )

    cancelled = engine.cancel_delegation(created.delegation_id)

    assert cancelled.status.value == "cancelled"
    assert {child.status.value for child in cancelled.children} == {"cancelled"}
    session = engine.channel.get_session(created.session_id)
    assert session is not None
    assert session.active_agent_id == "ceo"
    assert session.state == SessionStatus.OPEN


def test_child_results_complete_delegation_and_return_one_ceo_synthesis(engine):
    engine.projects.create(Project(
        id="vinted",
        name="Vinted",
        type=ProjectType.REVENUE,
    ))
    _install_ceo(engine, [DirectiveClassification(
        directive_type="informational",
        reasoning="needs coordinated review",
        primary_squad="strategy",
        agents_needed=["cmo", "cfo"],
        approval_tier="auto",
        route="answer",
    )])
    created = engine.process_directive(
        "Review campaign performance and budget",
        context=DirectiveContext(
            channel="board",
            account_id="local",
            chat_id="main",
            sender_id="founder",
            project_id="vinted",
        ),
    )
    delegation = engine.get_delegation(created.delegation_id)

    partial = engine.complete_delegated_task(
        delegation.id,
        delegation.children[0].id,
        {"summary": "Campaign conversion improved by 12%."},
    )
    completed = engine.complete_delegated_task(
        delegation.id,
        delegation.children[1].id,
        {"summary": "Budget remains within the approved cap."},
    )

    assert partial.status.value == "active"
    assert completed.status.value == "completed"
    assert completed.result["message"].startswith("ANSWER_MARKER")
    turns = engine.channel.session_turns(created.session_id)
    synthesis_turns = [
        turn for turn in turns
        if turn.kind == "delegation_result"
    ]
    assert len(synthesis_turns) == 1
    assert synthesis_turns[0].agent_id == "ceo"
    assert "Campaign conversion improved by 12%." in (
        engine.registry.get("ceo").answer_question
    )
    assert "Budget remains within the approved cap." in (
        engine.registry.get("ceo").answer_question
    )


def test_ceo_synthesis_failure_fails_delegation_without_clobbering_children(
    engine,
):
    engine.projects.create(Project(
        id="vinted",
        name="Vinted",
        type=ProjectType.REVENUE,
    ))
    _install_ceo(engine, [DirectiveClassification(
        directive_type="informational",
        reasoning="needs coordinated review",
        primary_squad="strategy",
        agents_needed=["cmo", "cfo"],
        approval_tier="auto",
        route="answer",
    )])
    created = engine.process_directive(
        "Review campaign performance and budget",
        context=DirectiveContext(
            channel="board",
            account_id="local",
            chat_id="main",
            sender_id="founder",
            project_id="vinted",
        ),
    )
    delegation = engine.get_delegation(created.delegation_id)
    engine.complete_delegated_task(
        delegation.id,
        delegation.children[0].id,
        {"summary": "Campaign review complete."},
    )

    def fail_synthesis(*args, **kwargs):
        raise RuntimeError("synthesis provider unavailable")

    engine.registry.get("ceo").answer = fail_synthesis
    failed = engine.complete_delegated_task(
        delegation.id,
        delegation.children[1].id,
        {"summary": "Budget review complete."},
    )

    assert failed.status.value == "failed"
    assert failed.result == {"error": "synthesis provider unavailable"}
    assert {
        child.status.value for child in failed.children
    } == {"completed"}
    assert not any(
        turn.kind == "delegation_result"
        for turn in engine.channel.session_turns(created.session_id)
    )


def test_single_specialist_handoff_owns_current_and_followup_turns(engine):
    first_route = DirectiveClassification(
        directive_type="informational",
        reasoning="campaign specialist requested",
        primary_squad="growth",
        agents_needed=["cmo"],
        approval_tier="auto",
        route="answer",
    )
    followup_route = DirectiveClassification(
        directive_type="informational",
        reasoning="same campaign intent",
        primary_squad="growth",
        agents_needed=[],
        approval_tier="auto",
        route="answer",
    )
    _install_ceo(engine, [first_route, followup_route])
    ceo_registry = engine.registry

    class FakeCMO:
        role = "cmo"
        display_name = "CMO"

        def __init__(self):
            self.prompts = []

        def call(self, prompt, **kwargs):
            from kompany.llm.client import LLMResponse

            self.prompts.append(prompt)
            return LLMResponse(
                text=f"CMO reply {len(self.prompts)}",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                model="fake",
            )

    cmo = FakeCMO()

    class HandoffRegistry:
        def get(self, role, company_state=None):
            if role == "cmo":
                return cmo
            return ceo_registry.get(role, company_state)

        def descriptor(self, role):
            return ceo_registry.descriptor(role)

    engine.registry = HandoffRegistry()
    context = DirectiveContext(
        channel="board",
        account_id="local",
        chat_id="main",
        sender_id="founder",
        project_id="vinted",
    )

    first = engine.process_directive(
        "Review the Vinted campaign",
        context=context,
    )

    assert first.status == "completed"
    assert first.message == "CMO reply 1"
    assert first.active_agent_id == "cmo"
    assert first.previous_agent_id == "ceo"
    assert first.handoff_id
    assert first.conversation_continues is True
    session = engine.channel.get_session(first.session_id)
    assert session.active_agent_id == "cmo"
    assert session.session_epoch == 1
    assert session.state == SessionStatus.OPEN
    assert engine.agent_status.get("cmo")["status"] == "idle"
    assert "You have no tools in this direct channel reply" in cmo.prompts[0]
    assert "Do not claim" in cmo.prompts[0]
    assert "Conversation so far:" in cmo.prompts[0]

    followup = engine.process_directive(
        "What should we change first?",
        session_id=first.session_id,
        context=context,
    )

    assert followup.message == "CMO reply 2"
    assert followup.active_agent_id == "cmo"
    assert followup.previous_agent_id is None
    assert followup.conversation_continues is True
    assert len(cmo.prompts) == 2
    assert "CMO: CMO reply 1" in cmo.prompts[1]
    specialist_turns = engine.channel.session_turns(first.session_id)
    assert specialist_turns[-1].agent_id == "cmo"


def test_failed_specialist_startup_rolls_back_conversation_owner(engine):
    _install_ceo(engine, [DirectiveClassification(
        directive_type="informational",
        reasoning="campaign specialist requested",
        primary_squad="growth",
        agents_needed=["cmo"],
        approval_tier="auto",
        route="answer",
    )])
    ceo_registry = engine.registry

    class FailingCMO:
        def call(self, prompt, **kwargs):
            raise RuntimeError("provider unavailable")

    class HandoffRegistry:
        def get(self, role, company_state=None):
            if role == "cmo":
                return FailingCMO()
            return ceo_registry.get(role, company_state)

        def descriptor(self, role):
            return ceo_registry.descriptor(role)

    engine.registry = HandoffRegistry()

    with pytest.raises(RuntimeError, match="provider unavailable"):
        engine.process_directive("Review the campaign")

    session = engine.channel.open_session()
    assert session.active_agent_id == "ceo"
    assert session.previous_agent_id is None
    assert session.handoff_id is None
    assert session.session_epoch == 0


def test_manual_agent_and_status_commands_use_persisted_owner(engine):
    context = DirectiveContext(
        channel="board",
        account_id="local",
        chat_id="main",
        sender_id="founder",
    )

    selected = engine.process_directive("/agent cmo", context=context)

    assert selected.status == "completed"
    assert selected.active_agent_id == "cmo"
    assert selected.previous_agent_id == "ceo"
    assert selected.conversation_continues is True
    assert "CMO" in selected.message

    status = engine.process_directive(
        "/status",
        session_id=selected.session_id,
        context=context,
    )

    assert status.active_agent_id == "cmo"
    assert status.conversation_continues is True
    assert "CMO" in status.message
    assert "General" in status.message

    returned = engine.process_directive(
        "/ceo",
        session_id=selected.session_id,
        context=context,
    )
    assert returned.active_agent_id == "ceo"
    assert returned.previous_agent_id == "cmo"
    assert returned.conversation_continues is True


def test_inbound_context_cannot_select_non_conversation_agent(engine):
    context = DirectiveContext(
        channel="board",
        account_id="local",
        chat_id="main",
        sender_id="founder",
        active_agent_id="writer",
    )

    result = engine.process_directive("/status", context=context)

    assert result.status == "failed"
    assert "cannot own" in result.message
    assert result.session_id is None


def test_manual_project_and_new_commands_switch_isolated_sessions(engine):
    engine.projects.create(
        Project(
            id="vinted",
            name="Vinted",
            type=ProjectType.REVENUE,
        )
    )
    context = DirectiveContext(
        channel="board",
        account_id="local",
        chat_id="main",
        sender_id="founder",
    )

    selected = engine.process_directive("/project vinted", context=context)

    assert selected.project_id == "vinted"
    assert selected.conversation_continues is True
    project_session = engine.channel.get_session(selected.session_id)
    assert project_session.project_id == "vinted"
    assert project_session.state == SessionStatus.OPEN

    fresh = engine.process_directive(
        "/new",
        session_id=selected.session_id,
        context=context,
    )

    assert fresh.session_id != selected.session_id
    assert fresh.project_id == "vinted"
    assert fresh.active_agent_id == "ceo"
    assert fresh.conversation_continues is True
    old_session = engine.channel.get_session(selected.session_id)
    assert old_session.state == SessionStatus.ABANDONED


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
