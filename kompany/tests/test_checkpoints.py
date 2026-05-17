"""Tests for checkpoint persistence."""

from __future__ import annotations

from kompany.state.checkpoints import CheckpointStore
from kompany.state.database import Database


def test_checkpoint_save_and_latest(tmp_path):
    checkpoints = CheckpointStore(Database(tmp_path))

    checkpoints.save("proj-1", {"step": "first"}, task_id="task-1", step_index=1)
    checkpoints.save("proj-1", {"step": "second"}, task_id="task-2", step_index=2)

    latest = checkpoints.latest("proj-1")
    assert latest is not None
    assert latest["project_id"] == "proj-1"
    assert latest["task_id"] == "task-2"
    assert latest["step_index"] == 2
    assert latest["state"] == {"step": "second"}


def test_checkpoint_latest_empty(tmp_path):
    checkpoints = CheckpointStore(Database(tmp_path))
    assert checkpoints.latest("missing") is None
