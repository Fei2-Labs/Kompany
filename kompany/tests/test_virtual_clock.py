"""Virtual clock (model D) — task-completion drives the day counter."""

from __future__ import annotations

import json
from datetime import date, timedelta

from kompany.state import virtual_clock
from kompany.state.database import Database


def _seed_targets(db, deadline: date) -> None:
    db.execute(
        """INSERT INTO company_config (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (
            "targets.agreed",
            json.dumps({
                "initial_budget": 100.0,
                "revenue_target": 1000.0,
                "customer_target": None,
                "deadline": deadline.isoformat(),
                "source": "agreed",
            }),
        ),
    )
    db.commit()


def test_get_elapsed_defaults_to_zero(tmp_path):
    db = Database(tmp_path)
    assert virtual_clock.get_elapsed(db) == 0


def test_get_budget_lazy_snapshots_from_deadline(tmp_path):
    db = Database(tmp_path)
    deadline = date.today() + timedelta(days=89)
    _seed_targets(db, deadline)
    # First call computes + persists.
    assert virtual_clock.get_budget(db) == 89
    # Subsequent call reads cached value even if "today" drifts.
    assert virtual_clock.get_budget(db) == 89


def test_get_budget_zero_when_no_deadline(tmp_path):
    db = Database(tmp_path)
    assert virtual_clock.get_budget(db) == 0


def test_tick_increments_and_audits(tmp_path):
    db = Database(tmp_path)

    class _FakeAudit:
        def __init__(self):
            self.events = []

        def record(self, event_type, action, *, detail=None, project_id=None):
            self.events.append({
                "event_type": event_type,
                "action": action,
                "detail": detail,
                "project_id": project_id,
            })

    audit = _FakeAudit()
    new = virtual_clock.tick(db, "task.completed", audit=audit, project_id="proj-1")
    assert new == 1
    again = virtual_clock.tick(db, "task.completed", audit=audit, project_id="proj-1")
    assert again == 2
    assert virtual_clock.get_elapsed(db) == 2
    assert len(audit.events) == 2
    assert audit.events[0]["event_type"] == "virtual_day.advanced"
    assert audit.events[1]["detail"]["current"] == 2


def test_tick_silent_without_audit(tmp_path):
    """Audit is optional — missing it must not crash the tick."""
    db = Database(tmp_path)
    assert virtual_clock.tick(db, "manual") == 1


def test_reset_wipes_counter_and_budget(tmp_path):
    db = Database(tmp_path)
    deadline = date.today() + timedelta(days=30)
    _seed_targets(db, deadline)
    virtual_clock.get_budget(db)  # snapshot
    virtual_clock.tick(db, "task.completed")
    virtual_clock.reset(db)
    assert virtual_clock.get_elapsed(db) == 0
    # Budget recomputes on next access.
    assert virtual_clock.get_budget(db) == 30


def test_runner_ticks_on_task_complete(tmp_path):
    """End-to-end: ProjectRunner executing a task ticks the clock."""
    from types import SimpleNamespace
    from kompany.core.runner import ProjectRunner
    from kompany.state.agent_status import AgentStatusStore
    from kompany.state.audit import AuditLog
    from kompany.state.checkpoints import CheckpointStore
    from kompany.state.memory import AgentMemory
    from kompany.state.models import Project, ProjectType, Task
    from kompany.state.projects import Projects

    class FakeAgent:
        def call(self, prompt, directive_id=None, max_tokens=4096, action_type=None):
            return SimpleNamespace(text="done", cost_usd=0.01)

    class FakeRegistry:
        def get(self, role, company_state=None):
            return FakeAgent()

    db = Database(tmp_path)
    engine = SimpleNamespace(
        db=db,
        projects=Projects(db),
        audit=AuditLog(db),
        agent_status=AgentStatusStore(db),
        checkpoints=CheckpointStore(db),
        memory=AgentMemory(db),
        registry=FakeRegistry(),
        get_company_state=lambda: {},
    )

    project = Project(
        name="Test",
        type=ProjectType.OPERATIONAL,
        plan={
            "week_plan": ["Mon: a", "Tue: b", "Wed: c"],
            "proposer_role": "ceo",
            "other_agents_involved": [],
            "source": "team_proposal_first_week",
        },
        assigned_agents=[],
    )
    engine.projects.create(project)
    ProjectRunner(engine).run(project.id)
    # 3-day week plan → 3 virtual days burned.
    assert virtual_clock.get_elapsed(db) == 3
