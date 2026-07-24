"""Tests for the ticker's soul_cycles action — recurring cycle task filing.

Locks the behavior: a soul with ``cycle_cadence.hours_cet`` matching the
current CET hour, whose ``project_name_substring`` matches an active
project, gets one cycle task filed per hour (idempotent within the hour).
No project / no matching hour / no cadence => no task. Broken soul YAML
is skipped, never fatal.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kompany.core.ticker import (
    _CYCLE_TASK_TITLE_PREFIX,
    _cycle_task_prompt,
    _find_cycle_project,
    _has_pending_cycle_task,
    _soul_cycle_cadence,
)
from kompany.state.models import Task, TaskStatus


# --- _soul_cycle_cadence -----------------------------------------------


def test_soul_cycle_cadence_reads_yaml(tmp_path: Path):
    yaml = tmp_path / "soul.yaml"
    yaml.write_text(
        "role: linkedin_growth\n"
        "display_name: LinkedIn Growth\n"
        "cycle_cadence:\n"
        "  project_name_substring: LinkedIn Growth\n"
        "  hours_cet: [9, 12, 15, 18]\n"
        "  max_comments_per_cycle: 5\n"
    )

    class _Soul:
        soul_yaml = yaml

    cadence = _soul_cycle_cadence(_Soul())
    assert cadence is not None
    assert cadence["hours_cet"] == [9, 12, 15, 18]
    assert cadence["project_name_substring"] == "LinkedIn Growth"


def test_soul_cycle_cadence_none_when_no_block(tmp_path: Path):
    yaml = tmp_path / "soul.yaml"
    yaml.write_text("role: x\ndisplay_name: X\n")

    class _Soul:
        soul_yaml = yaml

    assert _soul_cycle_cadence(_Soul()) is None


def test_soul_cycle_cadence_none_when_no_yaml():
    class _Soul:
        soul_yaml = None

    assert _soul_cycle_cadence(_Soul()) is None


# --- _find_cycle_project -----------------------------------------------


def test_find_cycle_project_matches_substring_case_insensitive():
    engine = MagicMock()
    p = MagicMock()
    p.name = "Execute: LinkedIn Growth — feifeiding"
    engine.projects.list_active.return_value = [p]
    cadence = {"project_name_substring": "linkedin growth"}
    assert _find_cycle_project(engine, cadence) is p


def test_find_cycle_project_none_when_no_match():
    engine = MagicMock()
    p = MagicMock()
    p.name = "Some other project"
    engine.projects.list_active.return_value = [p]
    assert _find_cycle_project(engine, {"project_name_substring": "LinkedIn"}) is None


def test_find_cycle_project_none_when_no_substring():
    engine = MagicMock()
    assert _find_cycle_project(engine, {}) is None
    engine.projects.list_active.assert_not_called()


# --- _has_pending_cycle_task -------------------------------------------


def test_has_pending_cycle_task_true_when_pending_match():
    engine = MagicMock()
    t = MagicMock()
    t.status = TaskStatus.PENDING
    t.assigned_agent = "linkedin_growth"
    t.title = f"{_CYCLE_TASK_TITLE_PREFIX} linkedin_growth daily growth cycle"
    engine.projects.list_tasks.return_value = [t]
    assert _has_pending_cycle_task(engine, "pid", "linkedin_growth") is True


def test_has_pending_cycle_task_false_when_completed():
    engine = MagicMock()
    t = MagicMock()
    t.status = TaskStatus.COMPLETED
    t.assigned_agent = "linkedin_growth"
    t.title = f"{_CYCLE_TASK_TITLE_PREFIX} linkedin_growth"
    engine.projects.list_tasks.return_value = [t]
    assert _has_pending_cycle_task(engine, "pid", "linkedin_growth") is False


def test_has_pending_cycle_task_false_when_different_role():
    engine = MagicMock()
    t = MagicMock()
    t.status = TaskStatus.PENDING
    t.assigned_agent = "ceo"
    t.title = f"{_CYCLE_TASK_TITLE_PREFIX} ceo"
    engine.projects.list_tasks.return_value = [t]
    assert _has_pending_cycle_task(engine, "pid", "linkedin_growth") is False


# --- _cycle_task_prompt ------------------------------------------------


def test_cycle_task_prompt_includes_role_and_limits():
    prompt = _cycle_task_prompt(
        "linkedin_growth",
        {"max_comments_per_cycle": 5, "max_original_posts_per_day": 2, "anti_repeat_days": 7},
    )
    assert "linkedin_growth" in prompt
    assert "5" in prompt
    assert "2" in prompt
    assert "7" in prompt
    assert "NOT_LOGGED_IN" in prompt
    assert "APPROVAL" in prompt


# --- Integration: the action files a task end-to-end -------------------


def test_action_soul_cycles_files_task(tmp_path: Path, monkeypatch):
    """End-to-end: a soul with a matching hour + project gets one cycle
    task filed; a second call in the same hour is idempotent."""
    from kompany.core.ticker import Ticker

    # A soul yaml with cycle_cadence.
    yaml = tmp_path / "soul.yaml"
    yaml.write_text(
        "role: linkedin_growth\n"
        "display_name: LinkedIn Growth\n"
        "cycle_cadence:\n"
        "  project_name_substring: LinkedIn Growth\n"
        "  hours_cet: [9, 12, 15, 18]\n"
        "  max_comments_per_cycle: 5\n"
        "  max_original_posts_per_day: 2\n"
        "  anti_repeat_days: 7\n"
    )

    class _FakeSoul:
        role = "linkedin_growth"
        soul_yaml = yaml

    monkeypatch.setattr(
        "kompany.plugins.loader.registered",
        lambda kind: [_FakeSoul()] if kind == "soul" else [],
    )
    # Force the current CET hour to one in the cadence list.
    monkeypatch.setattr("kompany.core.ticker._current_local_hour", lambda: 12)

    # Fake engine + project + task store.
    engine = MagicMock()
    engine.settings = MagicMock()
    engine.settings.tick_interval_seconds = 300
    engine.settings.daemon_auto_execute = True
    project = MagicMock()
    project.id = "proj-li"
    project.name = "Execute: LinkedIn Growth — feifeiding"
    engine.projects.list_active.return_value = [project]
    filed_tasks: list[Task] = []

    def _create_task(task):
        filed_tasks.append(task)
        return task

    engine.projects.create_task.side_effect = _create_task
    engine.projects.list_tasks.return_value = []
    engine.projects.update_task_status.return_value = None
    engine.audit.record.return_value = None
    engine.daemon_ticks = MagicMock()
    engine.daemon_ticks.record.return_value = MagicMock()

    ticker = Ticker(
        engine=engine,
        ticks=engine.daemon_ticks,
        tick_interval_seconds=300,
        auto_execute=True,
    )
    actions = ticker._action_soul_cycles()
    assert any("soul_cycle_filed:linkedin_growth:" in a for a in actions)
    assert len(filed_tasks) == 1
    assert filed_tasks[0].assigned_agent == "linkedin_growth"
    assert filed_tasks[0].title.startswith(_CYCLE_TASK_TITLE_PREFIX)

    # Second call same hour: list_tasks now returns the pending one → idempotent.
    filed_tasks.clear()
    pending = MagicMock()
    pending.status = TaskStatus.PENDING
    pending.assigned_agent = "linkedin_growth"
    pending.title = filed_tasks[0].title if False else f"{_CYCLE_TASK_TITLE_PREFIX} linkedin_growth daily growth cycle"
    engine.projects.list_tasks.return_value = [pending]
    actions2 = ticker._action_soul_cycles()
    assert not any("soul_cycle_filed" in a for a in actions2)
    assert len(filed_tasks) == 0


def test_action_soul_cycles_skips_when_hour_not_in_cadence(tmp_path: Path, monkeypatch):
    from kompany.core.ticker import Ticker

    yaml = tmp_path / "soul.yaml"
    yaml.write_text(
        "role: linkedin_growth\n"
        "display_name: LinkedIn Growth\n"
        "cycle_cadence:\n"
        "  project_name_substring: LinkedIn Growth\n"
        "  hours_cet: [9, 12, 15, 18]\n"
    )

    class _FakeSoul:
        role = "linkedin_growth"
        soul_yaml = yaml

    monkeypatch.setattr(
        "kompany.plugins.loader.registered",
        lambda kind: [_FakeSoul()] if kind == "soul" else [],
    )
    monkeypatch.setattr("kompany.core.ticker._current_local_hour", lambda: 3)  # not in list

    engine = MagicMock()
    engine.settings = MagicMock()
    engine.settings.tick_interval_seconds = 300
    engine.settings.daemon_auto_execute = True
    engine.projects.create_task.return_value = None
    engine.projects.list_tasks.return_value = []

    ticker = Ticker(engine=engine, ticks=MagicMock(), tick_interval_seconds=300, auto_execute=True)
    actions = ticker._action_soul_cycles()
    assert actions == []
    engine.projects.create_task.assert_not_called()


def test_action_soul_cycles_skips_when_no_matching_project(tmp_path: Path, monkeypatch):
    from kompany.core.ticker import Ticker

    yaml = tmp_path / "soul.yaml"
    yaml.write_text(
        "role: linkedin_growth\n"
        "display_name: LinkedIn Growth\n"
        "cycle_cadence:\n"
        "  project_name_substring: LinkedIn Growth\n"
        "  hours_cet: [9, 12, 15, 18]\n"
    )

    class _FakeSoul:
        role = "linkedin_growth"
        soul_yaml = yaml

    monkeypatch.setattr(
        "kompany.plugins.loader.registered",
        lambda kind: [_FakeSoul()] if kind == "soul" else [],
    )
    monkeypatch.setattr("kompany.core.ticker._current_local_hour", lambda: 12)

    engine = MagicMock()
    engine.settings = MagicMock()
    engine.settings.tick_interval_seconds = 300
    engine.settings.daemon_auto_execute = True
    other = MagicMock()
    other.name = "Totally unrelated project"
    engine.projects.list_active.return_value = [other]
    engine.projects.create_task.return_value = None

    ticker = Ticker(engine=engine, ticks=MagicMock(), tick_interval_seconds=300, auto_execute=True)
    actions = ticker._action_soul_cycles()
    assert actions == []
    engine.projects.create_task.assert_not_called()
