"""Unit tests for the approval thread + RPG inbox service layer.

Covers:
- State machine (every legal transition + every illegal-from-terminal cell)
- Comments CRUD + ordering
- Snooze due-detection + manual unsnooze
- Thread walk (predecessor chain + successor fan-out)
- Severity validation + invalid by_type rejection
"""

from __future__ import annotations

import pytest

from kompany.state.approvals import (
    COMMENT_BY_TYPES,
    ApprovalRequests,
    IllegalApprovalTransition,
)
from kompany.state.database import Database
from kompany.state.models import (
    APPROVAL_SEVERITIES,
    APPROVAL_TERMINAL_STATUSES,
    ApprovalRequest,
    ApprovalStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def svc(tmp_path):
    return ApprovalRequests(Database(tmp_path))


def _new(svc: ApprovalRequests, **kwargs) -> ApprovalRequest:
    """Helper: create a default pending approval with optional overrides."""
    base = dict(action_type="test", summary="please decide")
    base.update(kwargs)
    return svc.create(ApprovalRequest(**base))


# ---------------------------------------------------------------------------
# Severity validation
# ---------------------------------------------------------------------------


def test_create_rejects_invalid_severity(svc):
    with pytest.raises(ValueError, match="invalid severity"):
        svc.create(ApprovalRequest(action_type="t", summary="s", severity="urgent"))


def test_default_severity_is_medium(svc):
    request = _new(svc)
    assert request.severity == "medium"
    # All five canonical values are accepted.
    for sev in APPROVAL_SEVERITIES:
        _new(svc, severity=sev)


# ---------------------------------------------------------------------------
# State machine — legal transitions
# ---------------------------------------------------------------------------


def test_pending_to_approved_succeeds(svc):
    r = _new(svc)
    out = svc.approve(r.id)
    assert out is not None
    assert out.status == ApprovalStatus.APPROVED


def test_pending_to_rejected_succeeds(svc):
    r = _new(svc)
    out = svc.reject(r.id, reason="no")
    assert out.status == ApprovalStatus.REJECTED
    assert out.resolution_reason == "no"


def test_pending_to_revision_requested_succeeds(svc):
    r = _new(svc)
    out = svc.request_revision(r.id, comment_body="try half the spend")
    assert out.status == ApprovalStatus.REVISION_REQUESTED
    # Comment lands on the original.
    comments = svc.list_comments(r.id)
    assert any("try half" in c.body for c in comments)


def test_pending_to_snoozed_succeeds(svc):
    r = _new(svc)
    out = svc.snooze(r.id, minutes=30)
    assert out.status == ApprovalStatus.SNOOZED
    assert out.snoozed_until is not None
    # The canonical "snoozed for 30m" line is written.
    bodies = [c.body for c in svc.list_comments(r.id)]
    assert any(b == "snoozed for 30m" for b in bodies)


def test_pending_to_cancelled_succeeds(svc):
    r = _new(svc)
    out = svc.cancel(r.id, reason="forget it")
    assert out.status == ApprovalStatus.CANCELLED
    assert out.resolution_reason == "forget it"


def test_snoozed_to_pending_via_unsnooze(svc):
    r = _new(svc)
    svc.snooze(r.id, minutes=5)
    back = svc.unsnooze(r.id)
    assert back.status == ApprovalStatus.PENDING
    assert back.snoozed_until is None


def test_snoozed_to_terminal_states_succeed(svc):
    # snoozed -> approved
    r = _new(svc)
    svc.snooze(r.id, minutes=5)
    out = svc.approve(r.id)
    assert out.status == ApprovalStatus.APPROVED

    # snoozed -> rejected
    r2 = _new(svc)
    svc.snooze(r2.id, minutes=5)
    assert svc.reject(r2.id).status == ApprovalStatus.REJECTED

    # snoozed -> revision_requested
    r3 = _new(svc)
    svc.snooze(r3.id, minutes=5)
    assert svc.request_revision(r3.id, "x").status == ApprovalStatus.REVISION_REQUESTED

    # snoozed -> cancelled
    r4 = _new(svc)
    svc.snooze(r4.id, minutes=5)
    assert svc.cancel(r4.id).status == ApprovalStatus.CANCELLED


# ---------------------------------------------------------------------------
# State machine — illegal transitions (terminal -> anything)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "first,second",
    [
        # Self-loops (e.g. approve -> approve) are intentionally
        # idempotent and excluded; see ``test_re_approve_is_idempotent``.
        ("approved", "reject"),
        ("approved", "revision"),
        ("approved", "snooze"),
        ("approved", "cancel"),
        ("rejected", "approve"),
        ("rejected", "revision"),
        ("rejected", "snooze"),
        ("rejected", "cancel"),
        ("revision_requested", "approve"),
        ("revision_requested", "reject"),
        ("revision_requested", "snooze"),
        ("revision_requested", "cancel"),
        ("cancelled", "approve"),
        ("cancelled", "reject"),
        ("cancelled", "revision"),
        ("cancelled", "snooze"),
    ],
)
def test_terminal_transitions_are_blocked(svc, first, second):
    r = _new(svc)

    # Move to ``first`` terminal state.
    if first == "approved":
        svc.approve(r.id)
    elif first == "rejected":
        svc.reject(r.id)
    elif first == "revision_requested":
        svc.request_revision(r.id, "x")
    elif first == "cancelled":
        svc.cancel(r.id)
    else:
        raise AssertionError(first)

    # Attempt the illegal follow-up.
    with pytest.raises(IllegalApprovalTransition):
        if second == "approve":
            svc.approve(r.id)
        elif second == "reject":
            svc.reject(r.id)
        elif second == "revision":
            svc.request_revision(r.id, "y")
        elif second == "snooze":
            svc.snooze(r.id, minutes=5)
        elif second == "cancel":
            svc.cancel(r.id)


def test_re_approve_is_idempotent(svc):
    """Approving an already-approved row is a silent no-op (legacy contract)."""
    r = _new(svc)
    svc.approve(r.id)
    out = svc.approve(r.id)  # idempotent
    assert out is not None
    assert out.status == ApprovalStatus.APPROVED


def test_re_reject_is_idempotent(svc):
    r = _new(svc)
    svc.reject(r.id)
    out = svc.reject(r.id)
    assert out is not None
    assert out.status == ApprovalStatus.REJECTED


def test_terminal_statuses_match_spec():
    """Sanity check the canonical terminal-state set."""
    assert APPROVAL_TERMINAL_STATUSES == {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.REVISION_REQUESTED,
        ApprovalStatus.CANCELLED,
    }


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


def test_add_comment_rejects_invalid_by_type(svc):
    r = _new(svc)
    with pytest.raises(ValueError, match="invalid by_type"):
        svc.add_comment(r.id, body="hi", by_type="other")


def test_add_comment_rejects_empty_body(svc):
    r = _new(svc)
    with pytest.raises(ValueError):
        svc.add_comment(r.id, body="   ", by_type="user")


def test_comments_ordered_by_created_then_rowid(svc):
    r = _new(svc)
    svc.add_comment(r.id, "first", "user")
    svc.add_comment(r.id, "second", "agent", by_id="cfo")
    svc.add_comment(r.id, "third", "system")
    bodies = [c.body for c in svc.list_comments(r.id)]
    assert bodies == ["first", "second", "third"]


def test_request_revision_requires_comment(svc):
    r = _new(svc)
    with pytest.raises(ValueError):
        svc.request_revision(r.id, comment_body="")


def test_comment_by_types_match_spec():
    assert COMMENT_BY_TYPES == {"user", "agent", "system"}


# ---------------------------------------------------------------------------
# Snooze / unsnooze
# ---------------------------------------------------------------------------


def test_snooze_minutes_must_be_positive(svc):
    r = _new(svc)
    with pytest.raises(ValueError):
        svc.snooze(r.id, minutes=0)
    with pytest.raises(ValueError):
        svc.snooze(r.id, minutes=-5)


def test_list_due_snoozed_picks_up_lapsed_rows(svc):
    fresh = _new(svc)
    lapsed = _new(svc)
    svc.snooze(fresh.id, minutes=60)
    svc.snooze(lapsed.id, minutes=30)
    # Backdate the lapsed row so the watchdog scanner sees it.
    svc.db.execute(
        "UPDATE approval_requests SET snoozed_until = datetime('now', '-1 minutes') "
        "WHERE id = ?",
        (lapsed.id,),
    )
    svc.db.commit()
    due = svc.list_due_snoozed()
    assert [d.id for d in due] == [lapsed.id]


def test_unsnooze_on_non_snoozed_is_no_op(svc):
    r = _new(svc)
    # Without snooze first — should just return the pending row.
    back = svc.unsnooze(r.id)
    assert back.status == ApprovalStatus.PENDING


# ---------------------------------------------------------------------------
# Thread walk
# ---------------------------------------------------------------------------


def test_list_thread_returns_chain_oldest_first(svc):
    a = _new(svc, summary="A")
    b = svc.create(ApprovalRequest(
        action_type="test", summary="B", predecessor_id=a.id
    ))
    c = svc.create(ApprovalRequest(
        action_type="test", summary="C", predecessor_id=b.id
    ))

    # Seed from middle.
    chain = svc.list_thread(b.id)
    assert [r.summary for r in chain] == ["A", "B", "C"]

    # Seed from root.
    chain = svc.list_thread(a.id)
    assert [r.summary for r in chain] == ["A", "B", "C"]

    # Seed from tail.
    chain = svc.list_thread(c.id)
    assert [r.summary for r in chain] == ["A", "B", "C"]


def test_list_thread_survives_cycle(svc):
    """A corrupt cycle (a.predecessor=b, b.predecessor=a) must terminate."""
    a = _new(svc, summary="A")
    b = svc.create(ApprovalRequest(
        action_type="test", summary="B", predecessor_id=a.id
    ))
    # Corrupt: point a.predecessor to b.
    svc.db.execute(
        "UPDATE approval_requests SET predecessor_id = ? WHERE id = ?",
        (b.id, a.id),
    )
    svc.db.commit()
    chain = svc.list_thread(a.id)
    # No infinite loop, both rows present.
    assert len(chain) == 2
    assert {r.id for r in chain} == {a.id, b.id}


def test_list_thread_returns_empty_for_missing_id(svc):
    assert svc.list_thread("does-not-exist") == []


# ---------------------------------------------------------------------------
# Backward compatibility — ApprovalRequest with no severity
# ---------------------------------------------------------------------------


def test_old_call_signature_still_works(svc):
    """All pre-task call sites used the original 5-field minimum form."""
    r = svc.create(ApprovalRequest(
        action_type="directive_execution",
        summary="Approve directive",
        payload={"cost": 10},
        directive_id="dir-1",
        requested_by="AutonomyGate",
    ))
    fetched = svc.get(r.id)
    assert fetched is not None
    assert fetched.severity == "medium"
    assert fetched.predecessor_id is None
    assert fetched.snoozed_until is None
