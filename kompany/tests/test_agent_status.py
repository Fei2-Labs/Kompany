"""Tests for agent status persistence."""

from __future__ import annotations

from kompany.state.agent_status import AgentStatusStore
from kompany.state.database import Database


def test_agent_status_upsert_and_get(tmp_path):
    statuses = AgentStatusStore(Database(tmp_path))

    statuses.set("ceo", "thinking", "classifying")
    statuses.set("ceo", "idle")

    status = statuses.get("ceo")
    assert status is not None
    assert status["agent_role"] == "ceo"
    assert status["status"] == "idle"
    assert status["current_task"] is None


def test_agent_status_list_all(tmp_path):
    statuses = AgentStatusStore(Database(tmp_path))
    statuses.set("ceo", "idle")
    statuses.set("cfo", "working", "budget check")

    rows = statuses.list_all()
    assert [row["agent_role"] for row in rows] == ["ceo", "cfo"]
