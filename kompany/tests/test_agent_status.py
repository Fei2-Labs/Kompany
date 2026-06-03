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


class _RecordingHub:
    """Minimal EventHub stand-in that records published envelopes."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


def test_agent_activity_carries_run_id_inside_scope(tmp_path, monkeypatch):
    """An agent.activity event emitted inside a run_scope carries that
    run's id so SSE clients can demux concurrent sessions."""
    import kompany.core.event_hub as event_hub
    from kompany.core.run_context import run_scope

    hub = _RecordingHub()
    monkeypatch.setattr(event_hub, "get_event_hub", lambda: hub)

    statuses = AgentStatusStore(Database(tmp_path))
    with run_scope() as rid:
        statuses.set("ceo", "thinking", "classifying directive")

    assert hub.events, "expected an agent.activity event to be published"
    event_type, payload = hub.events[-1]
    assert event_type == "agent.activity"
    assert payload["run_id"] == rid
    # Additive only — the existing contract fields remain.
    assert payload["agent_role"] == "ceo"
    assert payload["status"] == "thinking"


def test_agent_activity_run_id_none_outside_scope(tmp_path, monkeypatch):
    """Outside any run_scope, run_id is None (bootstrap / ad-hoc sets)."""
    import kompany.core.event_hub as event_hub

    hub = _RecordingHub()
    monkeypatch.setattr(event_hub, "get_event_hub", lambda: hub)

    statuses = AgentStatusStore(Database(tmp_path))
    statuses.set("cfo", "idle")

    assert hub.events
    _event_type, payload = hub.events[-1]
    assert payload["run_id"] is None
