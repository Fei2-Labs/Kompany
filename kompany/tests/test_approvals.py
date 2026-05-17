"""Tests for approval request persistence."""

from __future__ import annotations

from kompany.state.approvals import ApprovalRequests
from kompany.state.database import Database
from kompany.state.models import ApprovalRequest, ApprovalStatus


def test_create_and_list_pending_approval(tmp_path):
    approvals = ApprovalRequests(Database(tmp_path))
    request = approvals.create(ApprovalRequest(
        action_type="directive_execution",
        summary="Approve directive",
        payload={"cost": 10},
        directive_id="dir-1",
        requested_by="AutonomyGate",
    ))

    pending = approvals.list_pending()

    assert len(pending) == 1
    assert pending[0].id == request.id
    assert pending[0].payload == {"cost": 10}


def test_approve_pending_request(tmp_path):
    approvals = ApprovalRequests(Database(tmp_path))
    request = approvals.create(ApprovalRequest(
        action_type="directive_execution",
        summary="Approve directive",
    ))

    resolved = approvals.approve(request.id)

    assert resolved is not None
    assert resolved.status == ApprovalStatus.APPROVED
    assert resolved.resolved_by == "master"
    assert approvals.list_pending() == []


def test_update_payload_merges_keys(tmp_path):
    approvals = ApprovalRequests(Database(tmp_path))
    request = approvals.create(ApprovalRequest(
        action_type="delivery_approval",
        summary="Approve delivery",
        payload={"project_id": "proj-1", "tasks_completed": 2},
    ))

    updated = approvals.update_payload(
        request.id,
        {"released_at": "2026-05-15T11:30:00", "released_by": "master"},
    )

    assert updated is not None
    assert updated.payload["project_id"] == "proj-1"
    assert updated.payload["tasks_completed"] == 2
    assert updated.payload["released_at"] == "2026-05-15T11:30:00"
    assert updated.payload["released_by"] == "master"
    # status untouched
    assert updated.status == ApprovalStatus.PENDING


def test_reject_pending_request(tmp_path):
    approvals = ApprovalRequests(Database(tmp_path))
    request = approvals.create(ApprovalRequest(
        action_type="directive_execution",
        summary="Approve directive",
    ))

    resolved = approvals.reject(request.id, reason="too risky")

    assert resolved is not None
    assert resolved.status == ApprovalStatus.REJECTED
    assert resolved.resolution_reason == "too risky"
    assert approvals.list_pending() == []
