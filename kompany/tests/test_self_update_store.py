"""SelfUpdateProposalStore roundtrip + migration (PRD D3)."""

from __future__ import annotations

import pytest

from kompany.state.database import Database
from kompany.state.self_update_proposals import SelfUpdateProposalStore


@pytest.fixture()
def store(tmp_path):
    return SelfUpdateProposalStore(Database(tmp_path))


def test_create_defaults(store):
    pid = store.create("Fix the docs typo")
    row = store.get(pid)
    assert row is not None
    assert row["id"] == pid
    assert row["instruction"] == "Fix the docs typo"
    assert row["branch"] == f"self-update/{pid}"
    assert row["status"] == "running"
    assert row["files_changed"] == []
    assert row["tier"] is None
    assert row["created_at"]


def test_create_with_explicit_branch_and_vehicle(store):
    pid = store.create("x", branch="self-update/custom", vehicle="claude_code")
    row = store.get(pid)
    assert row["branch"] == "self-update/custom"
    assert row["vehicle"] == "claude_code"


def test_update_roundtrip(store):
    pid = store.create("change something")
    row = store.update(
        pid,
        status="proposed",
        tier="t2",
        files_changed=["a.py", "b.py"],
        diff_stat="2 files changed",
        test_summary="PASSED",
        session_id="sess-1",
        cost_usd=0.42,
        approval_id="appr-1",
    )
    assert row["status"] == "proposed"
    assert row["tier"] == "t2"
    assert row["files_changed"] == ["a.py", "b.py"]
    assert row["diff_stat"] == "2 files changed"
    assert row["test_summary"] == "PASSED"
    assert row["session_id"] == "sess-1"
    assert row["cost_usd"] == pytest.approx(0.42)
    assert row["approval_id"] == "appr-1"


def test_update_rejects_unknown_field_and_bad_status(store):
    pid = store.create("x")
    with pytest.raises(ValueError, match="unknown"):
        store.update(pid, bogus="nope")
    with pytest.raises(ValueError, match="invalid status"):
        store.update(pid, status="exploded")


def test_get_missing_returns_none(store):
    assert store.get("doesnotexist") is None


def test_list_newest_first_with_limit(store):
    ids = [store.create(f"instruction {i}") for i in range(5)]
    rows = store.list(limit=3)
    assert len(rows) == 3
    assert [r["id"] for r in rows] == list(reversed(ids))[:3]
