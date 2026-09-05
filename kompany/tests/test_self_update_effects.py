"""Approve/reject effects for self_update_proposal cards (PR2).

Fake subprocess for git push / gh — no network, no real origin.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from kompany.core import approval_effects
from kompany.core.self_update import workspace as ws
from kompany.core.self_update.effects import (
    ACTION_SELF_UPDATE,
    approve_self_update,
    reject_self_update,
)
from kompany.state.approvals import ApprovalRequests
from kompany.state.database import Database
from kompany.state.models import ApprovalRequest
from kompany.state.self_update_proposals import SelfUpdateProposalStore


class FakeAudit:
    def __init__(self):
        self.events: list[tuple[str, str]] = []

    def record(self, event_type, description, **kw):
        self.events.append((event_type, description))


class FakeEngine:
    def __init__(self, tmp_path):
        self.db = Database(tmp_path / "db")
        self.approvals = ApprovalRequests(self.db)
        self.self_update_proposals = SelfUpdateProposalStore(self.db)
        self.audit = FakeAudit()
        self.settings = SimpleNamespace(data_dir=tmp_path / "data")


def _seed(engine, tmp_path, monkeypatch) -> ApprovalRequest:
    """Proposal row + approval card + a clone dir that ensure_clone returns."""
    clone = tmp_path / "data" / "self_update" / "repo"
    clone.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "kompany.core.self_update.effects.ensure_clone", lambda d: clone
    )
    pid = engine.self_update_proposals.create("fix the thing")
    row = engine.self_update_proposals.get(pid)
    request = ApprovalRequest(
        action_type=ACTION_SELF_UPDATE,
        summary="Self-update proposal (t2): fix the thing",
        payload={
            "proposal_id": pid,
            "branch": row["branch"],
            "tier": "t2",
            "instruction": "fix the thing",
            "test_summary": "PASSED",
        },
        requested_by="self_update_pipeline",
    )
    engine.approvals.create(request)
    return request


@pytest.fixture(autouse=True)
def _maintainer_role(monkeypatch):
    """These tests exercise the push + PR path → trusted maintainer role
    with ambient credentials (07-24 installation role)."""
    from kompany.core.installation_role import InstallationRole

    r = InstallationRole("maintainer", "file", "/etc/kompany/installation_role", True, "test")
    monkeypatch.setattr("kompany.core.self_update.effects.resolve_installation_role", lambda: r)
    monkeypatch.setenv("KOMPANY_PROMOTION_TOKEN_FILE", "/nonexistent/promotion_token")


def _fake_run(push_rc=0, gh_rc=0, gh_url="https://github.com/x/pr/1"):
    def run(cmd, **kw):
        if "get-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "https://github.com/Fei2-Labs/Kompany.git\n", "")
        if cmd[0] == "git" and "push" in cmd:
            return subprocess.CompletedProcess(cmd, push_rc, "", "denied" if push_rc else "")
        if cmd[0] == "gh":
            return subprocess.CompletedProcess(cmd, gh_rc, gh_url + "\n", "no auth" if gh_rc else "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


def test_approve_pushes_and_records_pr(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path)
    request = _seed(engine, tmp_path, monkeypatch)
    monkeypatch.setattr(ws.subprocess, "run", _fake_run())
    out = approve_self_update(engine, request)
    assert out["status"] == "pushed"
    assert out["pr_url"] == "https://github.com/x/pr/1"
    row = engine.self_update_proposals.get(request.payload["proposal_id"])
    assert row["status"] == "approved"
    fresh = engine.approvals.get(request.id)
    assert fresh.payload["effect_applied"] is True
    assert fresh.payload["pr_url"] == "https://github.com/x/pr/1"


def test_approve_push_failure_no_stamp_retryable(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path)
    request = _seed(engine, tmp_path, monkeypatch)
    monkeypatch.setattr(ws.subprocess, "run", _fake_run(push_rc=1))
    out = approve_self_update(engine, request)
    assert out["status"] == "push_failed"
    fresh = engine.approvals.get(request.id)
    assert not (fresh.payload or {}).get("effect_applied")
    # Retry after auth fixed → succeeds and stamps.
    monkeypatch.setattr(ws.subprocess, "run", _fake_run())
    out2 = approve_self_update(engine, request)
    assert out2["status"] == "pushed"


def test_approve_gh_missing_still_pushes(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path)
    request = _seed(engine, tmp_path, monkeypatch)

    def run(cmd, **kw):
        if "get-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "https://github.com/Fei2-Labs/Kompany.git\n", "")
        if cmd[0] == "gh":
            raise FileNotFoundError("gh")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ws.subprocess, "run", run)
    out = approve_self_update(engine, request)
    assert out["status"] == "pushed"
    assert out["pr_url"] is None


def test_push_refuses_default_branch(tmp_path):
    ok, detail = ws.push_branch(tmp_path, "main")
    assert not ok and "default branch" in detail


def test_reject_keeps_branch_and_records(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path)
    request = _seed(engine, tmp_path, monkeypatch)
    calls: list[list[str]] = []

    def run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ws.subprocess, "run", run)
    out = reject_self_update(engine, request)
    assert out["status"] == "rejected"
    row = engine.self_update_proposals.get(request.payload["proposal_id"])
    assert row["status"] == "rejected"
    assert not any("push" in c for call in calls for c in call)


def test_dispatch_includes_self_update_action():
    assert ACTION_SELF_UPDATE in approval_effects.HARNESS_EFFECT_ACTIONS
    # Approve dispatch routes by action_type.
    req = ApprovalRequest(
        action_type=ACTION_SELF_UPDATE, summary="x", payload={}, requested_by="t"
    )
    engine = SimpleNamespace(self_update_proposals=None, settings=None)
    out = approval_effects.apply_post_approve_effect(engine, req)
    assert out["status"] == "invalid_payload"
    out = approval_effects.apply_post_reject_effect(
        SimpleNamespace(self_update_proposals=None, audit=FakeAudit()), req
    )
    assert out["status"] == "rejected"


# ---------------------------------------------------------------------------
# Four-interface parity on the self-update ops (PRD D6)
# ---------------------------------------------------------------------------


def test_self_update_ops_parity_sdk_rest_mcp(tmp_path, monkeypatch):
    """SDK / REST / MCP return the SAME dict for list/show (engine op is
    the single logic home; CLI renders the same engine call)."""
    import asyncio
    import json as _json

    from fastapi.testclient import TestClient

    import kompany.interfaces.api as api_mod
    from kompany.interfaces import mcp_proxy, mcp_server

    class OpsEngine:
        def __init__(self, db):
            self.self_update_proposals = SelfUpdateProposalStore(db)

        def self_update_list(self, limit=20):
            return self.self_update_proposals.list(limit=limit)

        def self_update_show(self, proposal_id):
            return self.self_update_proposals.get(proposal_id)

        async def start(self):  # api lifespan hook
            pass

        async def stop(self):
            pass

    db = Database(tmp_path / "db")
    engine = OpsEngine(db)
    pid = engine.self_update_proposals.create("parity check")

    # SDK path == engine op by construction; assert via engine directly.
    sdk_row = engine.self_update_show(pid)

    monkeypatch.setattr(api_mod, "_engine", engine)
    with TestClient(api_mod.app) as client:
        rest_row = client.get(f"/self-update/proposals/{pid}").json()
        rest_list = client.get("/self-update/proposals").json()

    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)
    monkeypatch.setattr(mcp_server, "_engine", engine)
    out = asyncio.run(
        mcp_server.call_tool("kompany_self_update_show", {"proposal_id": pid})
    )
    mcp_row = _json.loads(out[0].text)

    assert sdk_row == rest_row == mcp_row
    assert rest_list and rest_list[0]["id"] == pid
