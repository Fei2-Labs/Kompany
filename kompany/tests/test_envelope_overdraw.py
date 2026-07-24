"""Tests for allow_envelope_overdraw setting.

Default (False): exhausted envelope parks the task + proposes top-up
(existing hard-cap semantics). When True: task runs, audit records the
overdraw, token cost books to ledger as usual.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kompany.core.harness_execution.executor import execute_harness_task
from kompany.state.models import TaskStatus


def _make_engine(remaining: float, allow_overdraw: bool, data_dir: str) -> MagicMock:
    e = MagicMock()
    e.settings = MagicMock()
    e.settings.allow_envelope_overdraw = allow_overdraw
    # ensure_workspace(engine.settings.data_dir, ...) uses this as a real
    # path — MagicMock attribute access returns a string that becomes a
    # literal directory. Point it at a tmp dir so no artifacts leak into
    # the repo working tree.
    e.settings.data_dir = data_dir
    e.project_budget = MagicMock(return_value={"remaining": remaining})
    e.projects = MagicMock()
    e.projects.update_task_status = MagicMock()
    e.agent_status = MagicMock()
    e.agent_status.set = MagicMock()
    e.audit = MagicMock()
    e.audit.record = MagicMock()
    return e


def _make_task_project():
    task = MagicMock()
    task.id = "t1"
    task.title = "Soul cycle: linkedin_growth"
    task.assigned_agent = "linkedin_growth"
    task.budget_cap_usd = 0.5
    task.max_turns = 10
    project = MagicMock()
    project.id = "p1"
    project.name = "LinkedIn Growth"
    project.type.value = "growth"
    project.triggers_directive_id = None
    return task, project


def test_envelope_exhausted_parks_task_by_default(tmp_path):
    """Default (allow_envelope_overdraw=False): remaining<=0 parks the task."""
    engine = _make_engine(remaining=0.0, allow_overdraw=False, data_dir=str(tmp_path))
    task, project = _make_task_project()
    runner = MagicMock()
    runner.vehicle_name = "native"

    execute_harness_task(engine, runner, task, project, MagicMock())

    # Task status should NOT be set to ACTIVE (it was parked)
    engine.projects.update_task_status.assert_called_with(task.id, TaskStatus.PENDING)
    # Audit should record envelope_exhausted
    events = [c.kwargs.get("event_type") or c.args[0] for c in engine.audit.record.call_args_list]
    assert "task.envelope_exhausted" in events


def test_envelope_overdraw_allowed_runs_task(tmp_path):
    """allow_envelope_overdraw=True: remaining<=0 does NOT park; task runs."""
    engine = _make_engine(remaining=0.0, allow_overdraw=True, data_dir=str(tmp_path))
    task, project = _make_task_project()
    runner = MagicMock()
    runner.vehicle_name = "native"
    runner.start = MagicMock(return_value=MagicMock(
        final_text="done", cost_usd=0.01, tokens_in=100, tokens_out=50,
        exit_status="ok", session_id="s1", files_changed=[],
    ))

    execute_harness_task(engine, runner, task, project, MagicMock())

    # Task status should be set to ACTIVE (it ran)
    engine.projects.update_task_status.assert_any_call(task.id, TaskStatus.ACTIVE)
    # Audit should record overdraw (not exhausted)
    events = [c.kwargs.get("event_type") or c.args[0] for c in engine.audit.record.call_args_list]
    assert "task.envelope_overdraw" in events
    assert "task.envelope_exhausted" not in events


def test_envelope_positive_runs_normally_regardless_of_setting(tmp_path):
    """remaining > 0: task runs regardless of allow_envelope_overdraw."""
    for setting in (True, False):
        engine = _make_engine(remaining=5.0, allow_overdraw=setting, data_dir=str(tmp_path))
        task, project = _make_task_project()
        runner = MagicMock()
        runner.vehicle_name = "native"
        runner.start = MagicMock(return_value=MagicMock(
            final_text="done", cost_usd=0.01, tokens_in=100, tokens_out=50,
            exit_status="ok", session_id="s1", files_changed=[],
        ))

        execute_harness_task(engine, runner, task, project, MagicMock())

        engine.projects.update_task_status.assert_any_call(task.id, TaskStatus.ACTIVE)
