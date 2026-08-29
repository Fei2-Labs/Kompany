"""Tests for the PR4 harness execution wiring (core/harness_execution).

No subprocess, no LLM: a FakeRunner implements the HarnessRunner
protocol in memory and delivers scripted events through the real
``emit_event`` plumbing (so RunAbort kill semantics are exercised
exactly as a real vehicle would).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kompany.config.model_source import ModelSource
from kompany.core.harness import (
    ClaudeCodeRunner,
    CodexRunner,
    HarnessEvent,
    HarnessResult,
    OpencodeRunner,
    RunAbort,
)
from kompany.core.harness.runner_utils import emit_event
from kompany.core.harness_execution import (
    ACTION_BUDGET_INCREASE,
    ACTION_ENVELOPE_TOPUP,
    DEFAULT_BUDGET_CAP_USD,
    DEFAULT_MAX_TURNS,
    EventMonitor,
    classify_harness_outcome,
    execute_harness_task,
    execution_caps,
    harness_model,
    is_hard_failure,
    reset_vehicle_missing_dedupe,
    resolve_caps,
    select_runner,
)
from kompany.core.harness_execution.permission_gate import (
    PERMISSION_PROMPT_TOOL,
)
from kompany.core.runner import ProjectRunner, ProjectRunResult, TaskSpec
from kompany.state.agent_status import AgentStatusStore
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.checkpoints import CheckpointStore
from kompany.state.database import Database
from kompany.state.episodes import Episodes
from kompany.state.health_events import HealthEvents
from kompany.state.memory import AgentMemory
from kompany.state.models import Project, ProjectType, Task, TaskStatus
from kompany.state.projects import Projects


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRunner:
    """In-memory HarnessRunner: scripted events + canned result."""

    def __init__(self, result=None, events=(), vehicle="claude_code"):
        self.result = result if result is not None else HarnessResult()
        self.events = list(events)  # (kind, payload) tuples
        self._vehicle = vehicle
        self.start_calls: list[dict] = []
        self.resume_calls: list[dict] = []
        self.execution_context: dict = {}

    def bind_execution_context(self, **context):
        self.execution_context = context

    @property
    def vehicle_name(self) -> str:
        return self._vehicle

    def start(self, prompt, workspace, caps, on_event=None):
        self.start_calls.append(
            {"prompt": prompt, "workspace": workspace, "caps": caps}
        )
        self._deliver(on_event)
        return self.result

    def resume(self, session_id, prompt, workspace, caps, on_event=None):
        self.resume_calls.append(
            {"session_id": session_id, "prompt": prompt, "caps": caps}
        )
        self._deliver(on_event)
        return self.result

    def handoff(self, result):  # pragma: no cover — protocol completeness
        raise NotImplementedError

    def _deliver(self, on_event):
        # Real plumbing: RunAbort must propagate, consumer bugs must not.
        for kind, payload in self.events:
            emit_event(on_event, kind, payload)


class RaisingRunner(FakeRunner):
    def start(self, prompt, workspace, caps, on_event=None):
        raise RuntimeError("vehicle exploded")


class FakeCostTracker:
    def __init__(self):
        self.external_calls: list[dict] = []
        self.record_calls: list[dict] = []

    def record_external(self, **kwargs):
        self.external_calls.append(kwargs)
        return float(kwargs.get("cost_usd") or 0.0)

    def record(self, *args, **kwargs):
        self.record_calls.append(kwargs)
        return 0.0


class FakeAgent:
    calls = 0

    def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
        FakeAgent.calls += 1
        return SimpleNamespace(text="done", cost_usd=0.01)


class FakeRegistry:
    def get(self, role, company_state=None):
        return FakeAgent()


class FakeDelegations:
    def __init__(self):
        self.failed = []

    def get(self, delegation_id):
        return SimpleNamespace(context_packet={})

    def fail(self, delegation_id, error):
        self.failed.append((delegation_id, error))


class FakeEngine:
    def __init__(self, tmp_path, remaining=10.0, model_source=None):
        self.db = Database(tmp_path)
        self.projects = Projects(self.db)
        self.audit = AuditLog(self.db)
        self.agent_status = AgentStatusStore(self.db)
        self.checkpoints = CheckpointStore(self.db)
        self.memory = AgentMemory(self.db)
        self.episodes = Episodes(self.db)
        self.approvals = ApprovalRequests(self.db)
        self.health_events = HealthEvents(self.db)
        self.registry = FakeRegistry()
        self.delegations = FakeDelegations()
        self.reconciled_delegated_tasks = []
        self.reconcile_error = None
        self.cost_tracker = FakeCostTracker()
        self.remaining = remaining
        self.settings = SimpleNamespace(
            data_dir=tmp_path,
            model_primary="claude-sonnet-4-20250514",
            model_source=model_source,
            harness_execution_enabled=True,
            harness_permission_routing=True,
        )

    def project_budget(self, project_id):
        return {"project_id": project_id, "remaining": self.remaining}

    def get_company_state(self):
        return {}

    def reconcile_delegated_task(self, task_id):
        if self.reconcile_error:
            raise self.reconcile_error
        self.reconciled_delegated_tasks.append(task_id)

    def _fail_delegation_reconciliation(self, task, project, exc):
        return self.delegations.fail(task.delegation_id, str(exc))


def _make_project(engine, name="Revenue") -> Project:
    return engine.projects.create(
        Project(name=name, type=ProjectType.REVENUE, plan={})
    )


def _make_task(engine, project, **kwargs) -> Task:
    task = Task(
        project_id=project.id,
        title=kwargs.pop("title", "Build landing page"),
        assigned_agent=kwargs.pop("assigned_agent", "builder"),
        **kwargs,
    )
    return engine.projects.create_task(task)


# ---------------------------------------------------------------------------
# Vehicle selection
# ---------------------------------------------------------------------------


def test_select_runner_none_without_model_source():
    settings = SimpleNamespace(
        model_source=None, harness_execution_enabled=True,
        model_primary="claude-sonnet-4",
    )
    assert select_runner(settings) is None
    assert select_runner(None) is None


def test_select_runner_none_when_flag_off():
    settings = SimpleNamespace(
        model_source=ModelSource(kind="custom_api"),
        harness_execution_enabled=False,
        model_primary="gpt-5",
    )
    assert select_runner(settings) is None


def test_select_runner_vehicle_mapping(monkeypatch):
    # Hermetic: the mapping must not depend on which CLIs this machine has.
    monkeypatch.setattr(
        "kompany.core.harness_execution.selection.shutil.which",
        lambda binary: f"/usr/local/bin/{binary}",
    )
    cases = [
        (ModelSource(kind="claude_subscription", monthly_fee_usd=20),
         ClaudeCodeRunner),
        (ModelSource(kind="openai_subscription", monthly_fee_usd=20),
         CodexRunner),
        (ModelSource(kind="custom_api"), OpencodeRunner),
    ]
    for source, expected in cases:
        settings = SimpleNamespace(
            model_source=source,
            harness_execution_enabled=True,
            model_primary="claude-sonnet-4",
        )
        assert isinstance(select_runner(settings), expected)


def test_harness_model_strips_claude_code_prefix():
    settings = SimpleNamespace(model_primary="claude-code:sonnet")
    assert harness_model(settings, "claude_code") == "sonnet"
    # Other vehicles keep the tier model as-is.
    assert harness_model(settings, "codex") == "claude-code:sonnet"
    assert harness_model(SimpleNamespace(model_primary=""), "claude_code") == "sonnet"


# ---------------------------------------------------------------------------
# Caps (PRD D3)
# ---------------------------------------------------------------------------


def test_resolve_caps_defaults_and_bounds():
    assert resolve_caps(None, None) == (DEFAULT_BUDGET_CAP_USD, DEFAULT_MAX_TURNS)
    assert resolve_caps(2.0, 10) == (2.0, 10)
    # CEO can never assign past the hard upper bound.
    assert resolve_caps(50.0, 10) == (5.0, 10)
    # Zero/negative junk falls back to defaults.
    assert resolve_caps(0.0, 0) == (DEFAULT_BUDGET_CAP_USD, DEFAULT_MAX_TURNS)


def test_execution_caps_apply_defaults_but_never_the_ceiling():
    """Execution reads the task row as authoritative: a founder-approved
    cap above the CEO ceiling is honored as-is, never re-clamped."""
    assert execution_caps(None, None) == (
        DEFAULT_BUDGET_CAP_USD, DEFAULT_MAX_TURNS,
    )
    assert execution_caps(8.0, 10) == (8.0, 10)  # > MAX_BUDGET_CAP_USD
    assert execution_caps(0.0, 0) == (
        DEFAULT_BUDGET_CAP_USD, DEFAULT_MAX_TURNS,
    )


def test_founder_approved_cap_survives_next_run(tmp_path):
    """harness_budget_increase raised the row to $8 (past the $5 CEO
    ceiling) — the next run must use $8, not re-clamp to $5."""
    engine = FakeEngine(tmp_path, remaining=100.0)
    project = _make_project(engine)
    task = _make_task(engine, project, budget_cap_usd=0.5, max_turns=10)
    engine.projects.set_task_budget_cap(task.id, 8.0)  # approval effect
    row = engine.projects.get_task(task.id)
    runner = FakeRunner(result=HarnessResult(final_text="ok", cost_usd=0.01))
    execute_harness_task(
        engine, runner, row, project, ProjectRunResult(project_id=project.id)
    )
    caps = runner.start_calls[0]["caps"]
    assert caps.budget_cap_usd == pytest.approx(8.0)


def test_ceo_proposed_cap_still_clamps_at_decomposition(tmp_path, monkeypatch):
    """A CEO-proposed $50 cap writes a $5 row — the ceiling still binds
    the CEO at decomposition write time (founder approval is the only
    way past it)."""
    engine = FakeEngine(tmp_path)  # model_source None → legacy execution
    project = engine.projects.create(Project(
        name="Revenue", type=ProjectType.REVENUE,
        plan={"paths": [{"name": "Consulting"}]},
    ))
    runner = ProjectRunner(engine)
    monkeypatch.setattr(
        runner, "_decompose",
        lambda p: [TaskSpec(title="Huge", assigned_agent="builder", prompt="",
                            budget_cap_usd=50.0, max_turns=10)],
    )
    runner.run(project.id)
    row = engine.projects.list_tasks(project.id)[0]
    assert row.budget_cap_usd == pytest.approx(5.0)


def test_decomposed_tasks_persist_cap_defaults(tmp_path, monkeypatch):
    """TaskSpec caps (or defaults when the CEO omits them) must land on
    the task ROW — execution reads tasks from the DB, not the specs."""
    engine = FakeEngine(tmp_path)  # model_source None → legacy execution
    project = engine.projects.create(Project(
        name="Revenue", type=ProjectType.REVENUE,
        plan={"paths": [{"name": "Consulting"}]},
    ))
    runner = ProjectRunner(engine)
    monkeypatch.setattr(
        runner, "_decompose",
        lambda p: [
            TaskSpec(title="No caps", assigned_agent="writer", prompt=""),
            TaskSpec(title="With caps", assigned_agent="builder", prompt="",
                     budget_cap_usd=2.5, max_turns=12),
        ],
    )
    runner.run(project.id)
    by_title = {t.title: t for t in engine.projects.list_tasks(project.id)}
    assert by_title["No caps"].budget_cap_usd == DEFAULT_BUDGET_CAP_USD
    assert by_title["No caps"].max_turns == DEFAULT_MAX_TURNS
    assert by_title["With caps"].budget_cap_usd == 2.5
    assert by_title["With caps"].max_turns == 12


def test_week_plan_tasks_get_cap_defaults(tmp_path):
    engine = FakeEngine(tmp_path)
    project = engine.projects.create(Project(
        name="First move", type=ProjectType.OPERATIONAL,
        plan={"week_plan": ["Mon: a", "Tue: b"], "proposer_role": "cro",
              "other_agents_involved": [],
              "source": "team_proposal_first_week"},
    ))
    ProjectRunner(engine).run(project.id)
    for task in engine.projects.list_tasks(project.id):
        assert task.budget_cap_usd == DEFAULT_BUDGET_CAP_USD
        assert task.max_turns == DEFAULT_MAX_TURNS


# ---------------------------------------------------------------------------
# Envelope guard
# ---------------------------------------------------------------------------


def test_effective_cap_is_min_of_task_cap_and_envelope(tmp_path):
    engine = FakeEngine(tmp_path, remaining=0.3)
    project = _make_project(engine)
    task = _make_task(engine, project, budget_cap_usd=0.5, max_turns=10)
    runner = FakeRunner(result=HarnessResult(final_text="ok", cost_usd=0.01))
    execute_harness_task(
        engine, runner, task, project, ProjectRunResult(project_id=project.id)
    )
    assert len(runner.start_calls) == 1
    caps = runner.start_calls[0]["caps"]
    assert caps.budget_cap_usd == pytest.approx(0.3)
    assert caps.max_turns == 10


def test_envelope_exhausted_parks_task_with_one_topup_approval(tmp_path):
    engine = FakeEngine(tmp_path, remaining=0.0)
    project = _make_project(engine)
    task_a = _make_task(engine, project, title="Task A")
    task_b = _make_task(engine, project, title="Task B")
    runner = FakeRunner()
    result = ProjectRunResult(project_id=project.id)

    execute_harness_task(engine, runner, task_a, project, result)
    execute_harness_task(engine, runner, task_b, project, result)

    # No run happened at all.
    assert runner.start_calls == [] and runner.resume_calls == []
    # Tasks parked back to PENDING.
    statuses = {t.status for t in engine.projects.list_tasks(project.id)}
    assert statuses == {TaskStatus.PENDING}
    # Exactly ONE funding-path approval (no inbox spam), mission-integrity
    # wording proposes top-up / revenue, never a terminal refusal.
    pending = [
        a for a in engine.approvals.list_pending()
        if a.action_type == ACTION_ENVELOPE_TOPUP
    ]
    assert len(pending) == 1
    assert pending[0].project_id == project.id
    assert "Top up" in pending[0].summary
    assert "revenue" in pending[0].summary.lower()
    # The founder must SEE the amount before approving — the funded
    # amount comes from this payload, so it cannot be a hidden value.
    assert f"${DEFAULT_BUDGET_CAP_USD:.2f}" in pending[0].summary
    assert pending[0].payload["suggested_top_up_usd"] == pytest.approx(
        DEFAULT_BUDGET_CAP_USD
    )
    # Audit trail per parked task.
    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert events.count("task.envelope_exhausted") == 2


# ---------------------------------------------------------------------------
# Event monitor — republication, touch throttle, turn cap
# ---------------------------------------------------------------------------


class FakeHub:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    def publish(self, event_type, payload=None):
        self.published.append((event_type, dict(payload or {})))


class FakeProjects:
    def __init__(self):
        self.touches: list[str] = []
        self.session_writes: list[tuple[str, str, str]] = []

    def touch_task(self, task_id):
        self.touches.append(task_id)

    def set_task_harness_session(self, task_id, session_id, vehicle):
        self.session_writes.append((task_id, session_id, vehicle))


def _monitor(max_turns=50, clock=None, vehicle=None):
    hub = FakeHub()
    projects = FakeProjects()
    task = Task(project_id="p1", title="t", assigned_agent="builder")
    project = Project(id="p1", name="P", type=ProjectType.REVENUE)
    times = {"now": 0.0}
    monitor = EventMonitor(
        projects, task, project, max_turns,
        hub=hub, clock=clock or (lambda: times["now"]),
        vehicle=vehicle,
    )
    return monitor, hub, projects, times, task


def test_events_republished_with_activity_kind_harness():
    monitor, hub, _projects, _times, task = _monitor()
    monitor(HarnessEvent(kind="session_started", payload={"session_id": "s9"}))
    monitor(HarnessEvent(kind="text", payload={"text": "hello world"}))
    monitor(HarnessEvent(kind="tool_use", payload={"tool_name": "Write"}))

    assert [t for t, _ in hub.published] == ["harness.event"] * 3
    for _, payload in hub.published:
        assert payload["activity_kind"] == "harness"
        assert payload["project_id"] == "p1"
        assert payload["task_id"] == task.id
        assert payload["agent_role"] == "builder"
    assert hub.published[1][1]["summary"] == "hello world"
    assert hub.published[2][1]["summary"] == "Write"
    assert monitor.session_id == "s9"
    assert monitor.tool_events == 1


def test_session_started_persists_immediately_when_vehicle_given():
    # Stage A session-persistence gap: previously the session id was only
    # written to SQLite after the entire run finished. A crash mid-run
    # lost it, forcing the next attempt to runner.start() instead of
    # runner.resume(). EventMonitor must now write it the moment the
    # vehicle reports session_started, not at the end of the run.
    monitor, _hub, projects, _times, task = _monitor(vehicle="claude_code")
    assert projects.session_writes == []

    monitor(HarnessEvent(kind="session_started", payload={"session_id": "sess-live"}))

    assert projects.session_writes == [(task.id, "sess-live", "claude_code")]

    # A second session_started (e.g. reconnection) re-persists — harmless
    # idempotent overwrite of the same task row.
    monitor(HarnessEvent(kind="session_started", payload={"session_id": "sess-live-2"}))
    assert projects.session_writes[-1] == (task.id, "sess-live-2", "claude_code")


def test_session_started_no_write_without_vehicle():
    # Back-compat: callers that don't pass vehicle= (or old callers before
    # this change) must not crash — persistence is simply skipped.
    monitor, _hub, projects, _times, _task = _monitor(vehicle=None)
    monitor(HarnessEvent(kind="session_started", payload={"session_id": "s1"}))
    assert projects.session_writes == []
    assert monitor.session_id == "s1"


def test_task_touch_is_throttled_to_ten_seconds():
    monitor, _hub, projects, times, _task = _monitor()
    monitor(HarnessEvent(kind="text", payload={"text": "a"}))   # first → touch
    monitor(HarnessEvent(kind="text", payload={"text": "b"}))   # +0s → skipped
    times["now"] = 5.0
    monitor(HarnessEvent(kind="text", payload={"text": "c"}))   # +5s → skipped
    times["now"] = 11.0
    monitor(HarnessEvent(kind="text", payload={"text": "d"}))   # +11s → touch
    assert len(projects.touches) == 2


def test_turn_cap_raises_runabort():
    monitor, _hub, _projects, _times, _task = _monitor(max_turns=2)
    monitor(HarnessEvent(kind="turn"))
    monitor(HarnessEvent(kind="turn"))
    with pytest.raises(RunAbort):
        monitor(HarnessEvent(kind="turn"))


def test_turn_cap_kill_is_turn_cap_exit_not_failure(tmp_path):
    """A run killed by the external turn cap ends as a turn-cap exit
    (asset state, audit 'task.completed') — never FAILED."""
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project, max_turns=2)
    runner = FakeRunner(events=[
        ("session_started", {"session_id": "s-turncap"}),
        ("text", {"text": "partial work"}),
        ("turn", {}),
        ("turn", {}),
        ("turn", {}),  # 3rd turn > cap of 2 → RunAbort through emit_event
        ("text", {"text": "never delivered"}),
    ])
    result = ProjectRunResult(project_id=project.id)
    execute_harness_task(engine, runner, task, project, result)

    assert result.tasks_failed == 0
    assert result.tasks_completed == 1
    row = engine.projects.list_tasks(project.id)[0]
    assert row.status == TaskStatus.DELIVERED  # no diff/tool evidence
    assert row.result["exit_status"] == "error_max_turns"
    assert row.result["output"] == "partial work"
    events = [e["event_type"] for e in engine.audit.recent(limit=20)]
    assert "task.completed" in events
    assert "task.failed" not in events


# ---------------------------------------------------------------------------
# Cost booking + session persistence + resume
# ---------------------------------------------------------------------------


def test_record_external_called_with_project_and_estimate_flag(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project)
    runner = FakeRunner(result=HarnessResult(
        final_text="done", cost_usd=0.12, cost_is_estimate=True,
        tokens_in=100, tokens_out=50, session_id="s1",
    ))
    result = ProjectRunResult(project_id=project.id)
    execute_harness_task(engine, runner, task, project, result)

    assert len(engine.cost_tracker.external_calls) == 1
    call = engine.cost_tracker.external_calls[0]
    assert call["project_id"] == project.id
    assert call["is_estimate"] is True
    assert call["cost_usd"] == 0.12
    assert call["tokens_in"] == 100
    assert result.total_ai_cost == pytest.approx(0.12)
    # The legacy single-shot entry point must not have been used.
    assert engine.cost_tracker.record_calls == []


def test_already_recorded_runner_cost_is_not_booked_twice(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project)
    runner = FakeRunner(
        result=HarnessResult(
            final_text="done",
            cost_usd=0.12,
            cost_already_recorded=True,
            tokens_in=100,
            tokens_out=50,
        ),
        vehicle="native",
    )
    result = ProjectRunResult(project_id=project.id)

    execute_harness_task(engine, runner, task, project, result)

    assert engine.cost_tracker.external_calls == []
    assert result.total_ai_cost == pytest.approx(0.12)
    row = engine.projects.list_tasks(project.id)[0]
    assert row.result["cost"] == pytest.approx(0.12)


def test_session_id_persisted_and_resume_used_on_retry(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project)
    runner = FakeRunner(
        result=HarnessResult(final_text="done", session_id="sess-1"),
        vehicle="claude_code",
    )
    result = ProjectRunResult(project_id=project.id)
    execute_harness_task(engine, runner, task, project, result)

    row = engine.projects.list_tasks(project.id)[0]
    assert row.harness_session_id == "sess-1"
    assert row.harness_vehicle == "claude_code"

    # Retry with the same vehicle resumes the stored session.
    execute_harness_task(engine, runner, row, project, result)
    assert len(runner.start_calls) == 1
    assert len(runner.resume_calls) == 1
    assert runner.resume_calls[0]["session_id"] == "sess-1"


def test_vehicle_mismatch_starts_fresh_instead_of_resume(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(
        engine, project,
        harness_session_id="old-sess", harness_vehicle="codex",
    )
    runner = FakeRunner(vehicle="claude_code")
    execute_harness_task(
        engine, runner, task, project, ProjectRunResult(project_id=project.id)
    )
    assert len(runner.start_calls) == 1
    assert runner.resume_calls == []


def test_session_id_persisted_mid_run_before_result_returns(tmp_path):
    # Stage A session-persistence gap: the vehicle's session_started event
    # must land in SQLite the moment it streams, not only after start()/
    # resume() returns. Simulate a run that streams session_started and
    # THEN aborts (e.g. process kill mid-turn) — the session id must
    # already be durable so a restart can resume() instead of start()ing
    # a brand-new, context-less session.
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project)
    runner = FakeRunner(
        events=[("session_started", {"session_id": "sess-mid-run"})],
        result=HarnessResult(final_text="", session_id=None, exit_status="error"),
        vehicle="claude_code",
    )
    execute_harness_task(
        engine, runner, task, project, ProjectRunResult(project_id=project.id)
    )

    row = engine.projects.list_tasks(project.id)[0]
    # Persisted from the mid-run event, even though the final HarnessResult
    # itself carried no session_id (simulating a crash/abort before the
    # vehicle's own summary line arrived).
    assert row.harness_session_id == "sess-mid-run"
    assert row.harness_vehicle == "claude_code"


def test_budget_exceeded_pauses_to_pending_with_approval(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project, budget_cap_usd=0.5)
    runner = FakeRunner(result=HarnessResult(
        final_text="half done", cost_usd=0.62, session_id="s2",
        exit_status="budget_exceeded", error="cap blown",
    ))
    result = ProjectRunResult(project_id=project.id)
    execute_harness_task(engine, runner, task, project, result)

    # No auto-retry.
    assert len(runner.start_calls) == 1 and runner.resume_calls == []
    row = engine.projects.list_tasks(project.id)[0]
    assert row.status == TaskStatus.PENDING
    assert row.harness_session_id == "s2"  # resumable after approval
    pending = [
        a for a in engine.approvals.list_pending()
        if a.action_type == ACTION_BUDGET_INCREASE
    ]
    assert len(pending) == 1
    assert "$0.50" in pending[0].summary and "$0.62" in pending[0].summary
    assert pending[0].payload["session_id"] == "s2"
    # Spend so far is still booked; the task is neither done nor failed.
    assert result.total_ai_cost == pytest.approx(0.62)
    assert result.tasks_completed == 0 and result.tasks_failed == 0
    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "task.budget_exceeded" in events


# ---------------------------------------------------------------------------
# Evidence-based outcomes (PRD D6)
# ---------------------------------------------------------------------------


def test_outcome_files_changed_is_completed():
    outcome, _ = classify_harness_outcome(
        HarnessResult(final_text="built it", files_changed=["index.html"]),
        tool_events=0,
    )
    assert outcome == "completed"


def test_outcome_denials_are_blocked_with_tools_listed():
    outcome, action = classify_harness_outcome(
        HarnessResult(
            final_text="done!", files_changed=["x.md"],
            permission_denials=[{"tool_name": "Bash"}, {"tool_name": "WebFetch"}],
        ),
        tool_events=3,
    )
    assert outcome == "blocked"
    assert "Bash" in action and "WebFetch" in action


def test_outcome_text_only_is_delivered():
    outcome, _ = classify_harness_outcome(
        HarnessResult(final_text="I did everything, trust me"),
        tool_events=0,
    )
    assert outcome == "delivered"


def test_outcome_tool_acts_without_denials_is_completed():
    outcome, _ = classify_harness_outcome(
        HarnessResult(final_text="researched and summarized"),
        tool_events=4,
    )
    assert outcome == "completed"


def test_outcome_non_success_exit_is_not_completed():
    outcome, _ = classify_harness_outcome(
        HarnessResult(final_text="ran out", exit_status="error_max_turns"),
        tool_events=4,
    )
    assert outcome == "delivered"


def test_harness_outcomes_set_task_status(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project)
    runner = FakeRunner(result=HarnessResult(
        final_text="blocked work",
        permission_denials=[{"tool_name": "Bash"}],
    ))
    execute_harness_task(
        engine, runner, task, project, ProjectRunResult(project_id=project.id)
    )
    row = engine.projects.list_tasks(project.id)[0]
    assert row.status == TaskStatus.BLOCKED
    assert "Bash" in row.result["founder_action"]


# ---------------------------------------------------------------------------
# Failure + legacy regression
# ---------------------------------------------------------------------------


def test_runner_exception_uses_existing_failed_handling(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project)
    result = ProjectRunResult(project_id=project.id)
    execute_harness_task(engine, RaisingRunner(), task, project, result)

    assert result.tasks_failed == 1
    row = engine.projects.list_tasks(project.id)[0]
    assert row.status == TaskStatus.FAILED
    assert "vehicle exploded" in row.result["error"]
    assert engine.checkpoints.latest(project.id) is not None
    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "task.failed" in events
    assert engine.agent_status.get("builder")["status"] == "idle"


def test_legacy_path_when_model_source_none(tmp_path, monkeypatch):
    """model_source=None → the byte-for-byte legacy agent.call path:
    single-shot call happens, record_external is never touched."""
    FakeAgent.calls = 0
    engine = FakeEngine(tmp_path, model_source=None)
    project = engine.projects.create(Project(
        name="Legacy", type=ProjectType.REVENUE,
        plan={"paths": [{"name": "Consulting"}]},
    ))
    runner = ProjectRunner(engine)
    monkeypatch.setattr(
        runner, "_decompose",
        lambda p: [TaskSpec(title="Write offer", assigned_agent="writer", prompt="")],
    )
    result = runner.run(project.id)

    assert FakeAgent.calls == 1
    assert result.tasks_completed == 1
    assert engine.cost_tracker.external_calls == []
    row = engine.projects.list_tasks(project.id)[0]
    assert row.status == TaskStatus.DELIVERED  # legacy classifier path


def test_harness_path_via_project_runner(tmp_path, monkeypatch):
    """End-to-end through ProjectRunner.run with a configured source:
    the harness path executes instead of agent.call."""
    FakeAgent.calls = 0
    engine = FakeEngine(
        tmp_path, model_source=ModelSource(kind="custom_api")
    )
    project = engine.projects.create(Project(
        name="Harness", type=ProjectType.REVENUE,
        plan={"paths": [{"name": "Consulting"}]},
    ))
    fake = FakeRunner(result=HarnessResult(
        final_text="built", files_changed=["site/index.html"],
        cost_usd=0.05, session_id="s3",
    ))
    monkeypatch.setattr(
        "kompany.core.runner.select_runner", lambda s, **kw: fake
    )
    runner = ProjectRunner(engine)
    monkeypatch.setattr(
        runner, "_decompose",
        lambda p: [TaskSpec(title="Build site", assigned_agent="builder", prompt="")],
    )
    result = runner.run(project.id)

    assert FakeAgent.calls == 0
    assert len(fake.start_calls) == 1
    # Workspace note made it into the session prompt.
    assert "workspace" in fake.start_calls[0]["prompt"].lower()
    assert result.tasks_completed == 1
    row = engine.projects.list_tasks(project.id)[0]
    assert row.status == TaskStatus.COMPLETED  # diff evidence → completed
    assert row.harness_session_id == "s3"


def test_soul_cycle_completion_persists_observation_and_episode(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = Task(
        project_id=project.id,
        title="Soul cycle: linkedin_growth daily growth cycle",
        assigned_agent="linkedin_growth",
    )
    engine.projects.create_task(task)
    result = ProjectRunResult(project_id=project.id)
    runner = FakeRunner(result=HarnessResult(
        final_text='{"actions": [], "metrics": {"followers": 10}}',
        files_changed=[],
        cost_usd=0.04,
        session_id="cycle-dry-run",
    ))

    execute_harness_task(engine, runner, task, project, result)

    observations = engine.memory.recall(
        "linkedin_growth", category="observation", query="cycle result"
    )
    assert observations
    assert "cost_usd=0.040000" in observations[0]["content"]
    episode = engine.episodes.get(project.id)
    assert episode is not None
    assert "linkedin_growth" in episode["payload_json"]


def test_soul_cycle_prompt_and_prior_observation_reach_runner(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine, name="LinkedIn Growth")
    engine.memory.remember(
        "linkedin_growth",
        "Cycle result: profile views rose after governance comments.",
        category="observation",
        context=f"project:{project.id}",
    )
    task = _make_task(
        engine,
        project,
        title="Soul cycle: linkedin_growth daily growth cycle",
        assigned_agent="linkedin_growth",
    )
    engine.projects.update_task_status(
        task.id,
        TaskStatus.PENDING,
        result={
            "cycle_prompt": "DRY-RUN ONLY: call linkedin.metrics.",
            "cycle_controls": {
                "scheduler_mode": "dry_run",
                "max_external_proposals_per_cycle": 1,
            },
        },
    )
    task = engine.projects.get_task(task.id)
    result = ProjectRunResult(project_id=project.id)
    runner = FakeRunner(result=HarnessResult(final_text="dry run", cost_usd=0.01))

    execute_harness_task(engine, runner, task, project, result)

    prompt = runner.start_calls[0]["prompt"]
    assert "DRY-RUN ONLY: call linkedin.metrics." in prompt
    assert "profile views rose after governance comments" in prompt
    assert runner.execution_context == {
        "project_id": project.id,
        "task_id": task.id,
        "agent_role": "linkedin_growth",
        "cycle_controls": {
            "scheduler_mode": "dry_run",
            "max_external_proposals_per_cycle": 1,
        },
        "pending_approval_ids": [],
    }


# ---------------------------------------------------------------------------
# Graceful degrade — missing vehicle binary (PR5)
# ---------------------------------------------------------------------------


def _patch_which_missing(monkeypatch):
    monkeypatch.setattr(
        "kompany.core.harness_execution.selection.shutil.which",
        lambda binary: None,
    )


def test_missing_binary_returns_none_and_records_health_event_once(
    tmp_path, monkeypatch
):
    _patch_which_missing(monkeypatch)
    reset_vehicle_missing_dedupe()
    engine = FakeEngine(tmp_path)
    settings = SimpleNamespace(
        model_source=ModelSource(kind="claude_subscription", monthly_fee_usd=20),
        harness_execution_enabled=True,
        model_primary="claude-sonnet-4",
    )
    assert select_runner(settings, health_events=engine.health_events) is None
    # Second selection in the same run: degrade again, but no event spam.
    assert select_runner(settings, health_events=engine.health_events) is None

    events = engine.health_events.list(kind="harness_vehicle_missing")
    assert len(events) == 1
    assert events[0]["detail"]["vehicle"] == "claude_code"
    assert events[0]["detail"]["binary"] == "claude"
    assert events[0]["detail"]["degraded_to"] == "single_call_legacy_path"
    # Actionable: the payload names the missing binary AND tells the
    # founder how to fix it.
    assert "claude" in events[0]["detail"]["hint"]
    assert "install" in events[0]["detail"]["hint"].lower()
    reset_vehicle_missing_dedupe()


def test_missing_binary_degrades_to_legacy_path_no_crash(
    tmp_path, monkeypatch
):
    """ModelSource set but CLI absent → legacy single-call path runs the
    task, a health event is recorded, nothing crashes (PRD acceptance)."""
    _patch_which_missing(monkeypatch)
    reset_vehicle_missing_dedupe()
    FakeAgent.calls = 0
    engine = FakeEngine(
        tmp_path,
        model_source=ModelSource(kind="claude_subscription", monthly_fee_usd=20),
    )
    project = engine.projects.create(Project(
        name="Degrade", type=ProjectType.REVENUE,
        plan={"paths": [{"name": "Consulting"}]},
    ))
    runner = ProjectRunner(engine)
    monkeypatch.setattr(
        runner, "_decompose",
        lambda p: [TaskSpec(title="Write offer", assigned_agent="writer", prompt="")],
    )
    result = runner.run(project.id)

    assert FakeAgent.calls == 1  # legacy single-call executed
    assert result.tasks_completed == 1
    assert engine.cost_tracker.external_calls == []  # no harness booking
    events = engine.health_events.list(kind="harness_vehicle_missing")
    assert len(events) == 1
    reset_vehicle_missing_dedupe()


# ---------------------------------------------------------------------------
# Honest error mapping (PR5) — hard error/timeout → FAILED
# ---------------------------------------------------------------------------


def test_is_hard_failure_matrix():
    cases = [
        (HarnessResult(exit_status="error", error="boom"), True),
        (HarnessResult(exit_status="timeout", error="wall clock"), True),
        # Work evidence despite the error → not a hard failure.
        (HarnessResult(exit_status="error", error="boom",
                       files_changed=["a.md"]), False),
        # Cap exits keep their pause/delivered semantics.
        (HarnessResult(exit_status="budget_exceeded", error="cap"), False),
        (HarnessResult(exit_status="error_max_turns", error="turns"), False),
        (HarnessResult(exit_status="success"), False),
    ]
    for result, expected in cases:
        assert is_hard_failure(result) is expected, result.exit_status


@pytest.mark.parametrize("exit_status", ["error", "timeout"])
def test_hard_error_without_evidence_marks_task_failed(tmp_path, exit_status):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project)
    runner = FakeRunner(result=HarnessResult(
        final_text="", session_id="s-err", cost_usd=0.03,
        exit_status=exit_status, error="claude CLI exited 1",
    ))
    result = ProjectRunResult(project_id=project.id)
    execute_harness_task(engine, runner, task, project, result)

    assert result.tasks_failed == 1 and result.tasks_completed == 0
    row = engine.projects.list_tasks(project.id)[0]
    assert row.status == TaskStatus.FAILED
    assert "claude CLI exited 1" in row.result["error"]
    assert row.result["exit_status"] == exit_status
    # Cost was real even though the run failed; session stays resumable.
    assert result.total_ai_cost == pytest.approx(0.03)
    assert row.harness_session_id == "s-err"
    assert engine.checkpoints.latest(project.id) is not None
    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "task.failed" in events
    assert "task.completed" not in events
    assert engine.agent_status.get("builder")["status"] == "idle"


def test_harness_failure_pushes_delegated_child_result(tmp_path):
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project, delegation_id="d-1")
    runner = FakeRunner(result=HarnessResult(
        final_text="",
        session_id="s-err",
        cost_usd=0.03,
        exit_status="error",
        error="vehicle failed",
    ))

    execute_harness_task(
        engine,
        runner,
        task,
        project,
        ProjectRunResult(project_id=project.id),
    )

    assert engine.projects.get_task(task.id).status == TaskStatus.FAILED
    assert engine.reconciled_delegated_tasks == [task.id]


def test_harness_reconciliation_failure_preserves_child_failure(tmp_path):
    engine = FakeEngine(tmp_path)
    engine.reconcile_error = RuntimeError("delegation store unavailable")
    project = _make_project(engine)
    task = _make_task(engine, project, delegation_id="d-1")
    runner = FakeRunner(result=HarnessResult(
        final_text="",
        session_id="s-err",
        cost_usd=0.03,
        exit_status="error",
        error="vehicle failed",
    ))
    result = ProjectRunResult(project_id=project.id)

    execute_harness_task(engine, runner, task, project, result)

    persisted = engine.projects.get_task(task.id)
    assert persisted.status == TaskStatus.FAILED
    assert persisted.result["error"] == "vehicle failed"
    assert result.tasks_failed == 1
    assert engine.delegations.failed == [
        ("d-1", "delegation store unavailable"),
    ]


def test_error_with_diff_evidence_stays_delivered(tmp_path):
    """Work happened before the CLI died → asset state, not a failure."""
    engine = FakeEngine(tmp_path)
    project = _make_project(engine)
    task = _make_task(engine, project)
    runner = FakeRunner(result=HarnessResult(
        final_text="partial", files_changed=["site/index.html"],
        exit_status="error", error="stream cut",
    ))
    result = ProjectRunResult(project_id=project.id)
    execute_harness_task(engine, runner, task, project, result)

    assert result.tasks_failed == 0 and result.tasks_completed == 1
    row = engine.projects.list_tasks(project.id)[0]
    assert row.status == TaskStatus.DELIVERED


# ---------------------------------------------------------------------------
# Permission routing args (PR5, PRD D5) — claude vehicle only, flag-gated
# ---------------------------------------------------------------------------


def _start_caps(engine, vehicle="claude_code"):
    project = _make_project(engine)
    task = _make_task(engine, project)
    runner = FakeRunner(
        result=HarnessResult(final_text="ok"), vehicle=vehicle
    )
    execute_harness_task(
        engine, runner, task, project, ProjectRunResult(project_id=project.id)
    )
    return runner.start_calls[0]["caps"], task, project


def test_claude_runner_receives_routing_args_when_flag_on(tmp_path):
    import json as _json

    engine = FakeEngine(tmp_path)
    caps, task, project = _start_caps(engine)

    args = caps.extra_cli_args
    assert args[args.index("--permission-prompt-tool") + 1] == (
        PERMISSION_PROMPT_TOOL
    )
    assert "--strict-mcp-config" in args
    mcp_config = _json.loads(args[args.index("--mcp-config") + 1])
    server = mcp_config["mcpServers"]["kompany"]
    assert server["args"] == ["-m", "kompany.interfaces.mcp_server"]
    assert server["env"]["KOMPANY_GATE_PROJECT_ID"] == project.id
    assert server["env"]["KOMPANY_GATE_TASK_ID"] == task.id
    assert server["env"]["KOMPANY_GATE_AGENT"] == "builder"


def test_no_routing_args_when_flag_off(tmp_path):
    engine = FakeEngine(tmp_path)
    engine.settings.harness_permission_routing = False
    caps, _task, _project = _start_caps(engine)
    assert caps.extra_cli_args == []


def test_no_routing_args_for_non_claude_vehicles(tmp_path):
    engine = FakeEngine(tmp_path)
    caps, _task, _project = _start_caps(engine, vehicle="opencode")
    assert caps.extra_cli_args == []


def test_settings_yaml_loads_harness_flags(tmp_path):
    from kompany.config.settings import KompanySettings

    config = tmp_path / "kompany.yaml"
    config.write_text(
        "company:\n  name: TestCo\n"
        "harness_execution_enabled: false\n"
        "harness_permission_routing: false\n",
        encoding="utf-8",
    )
    settings = KompanySettings.load(str(config))
    assert settings.harness_execution_enabled is False
    assert settings.harness_permission_routing is False
    # Defaults stay ON when the YAML omits the flags.
    bare = tmp_path / "bare.yaml"
    bare.write_text("company:\n  name: TestCo\n", encoding="utf-8")
    defaults = KompanySettings.load(str(bare))
    assert defaults.harness_execution_enabled is True
    assert defaults.harness_permission_routing is True
