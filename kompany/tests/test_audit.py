"""Tests for audit log persistence."""

from __future__ import annotations

from kompany.state.audit import AuditLog
from kompany.state.database import Database


def test_audit_record_and_recent(tmp_path):
    audit = AuditLog(Database(tmp_path))

    audit.record(
        "directive.received",
        "Received directive",
        detail={"input_length": 12},
        agent_role="ceo",
        directive_id="dir-1",
        project_id="proj-1",
    )

    events = audit.recent()
    assert len(events) == 1
    assert events[0]["event_type"] == "directive.received"
    assert events[0]["agent_role"] == "ceo"
    assert events[0]["directive_id"] == "dir-1"
    assert '"input_length": 12' in events[0]["detail"]
