"""07-24 installation role: tamper-proof role file + promotion gates."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from kompany.core import installation_role as ir
from kompany.core.self_update import promotion as pm
from kompany.core.self_update import workspace as ws
from kompany.core.self_update.effects import ACTION_SELF_UPDATE, approve_self_update
from kompany.state.approvals import ApprovalRequests
from kompany.state.database import Database
from kompany.state.models import ApprovalRequest
from kompany.state.self_update_proposals import SelfUpdateProposalStore


# ---------------------------------------------------------------------------
# role file resolution
# ---------------------------------------------------------------------------

def test_missing_file_defaults_to_customer(tmp_path):
    r = ir.resolve_installation_role(tmp_path / "nope")
    assert r.role == "customer" and r.source == "default" and r.trusted and not r.can_promote


def test_user_owned_file_is_untrusted_and_downgrades(tmp_path):
    f = tmp_path / "installation_role"; f.write_text("maintainer\n")
    r = ir.resolve_installation_role(f)  # owned by the test user == "daemon" user
    assert r.role == "customer" and not r.trusted and "owned by the daemon user" in r.reason
    # explicit operator-side override of the owner check (tests / dev boxes)
    r2 = ir.resolve_installation_role(f, require_privileged_owner=False)
    assert r2.role == "maintainer" and r2.can_promote


def test_world_writable_or_garbage_file_is_untrusted(tmp_path):
    f = tmp_path / "installation_role"; f.write_text("maintainer\n"); os.chmod(f, 0o666)
    assert not ir.resolve_installation_role(f).trusted
    g = tmp_path / "bad"; g.write_text("root\n")
    r = ir.resolve_installation_role(g, require_privileged_owner=False)
    assert r.role == "customer" and not r.trusted and "expected one of" in r.reason


def test_env_override_path_and_writer(tmp_path, monkeypatch):
    target = tmp_path / "etc" / "installation_role"
    monkeypatch.setenv(ir.ROLE_FILE_ENV, str(target))
    assert ir.role_file_path() == target
    p = ir.write_role_file("contributor")
    assert p == target and p.read_text().strip() == "contributor"
    with pytest.raises(ValueError):
        ir.write_role_file("root")


# ---------------------------------------------------------------------------
# promotion gates (pure)
# ---------------------------------------------------------------------------

def test_repo_slug_and_destination_gate(tmp_path, monkeypatch):
    assert pm.repo_slug("https://github.com/Fei2-Labs/Kompany.git") == "Fei2-Labs/Kompany"
    assert pm.repo_slug("git@github.com:Fei2-Labs/kompany-pro.git") == "Fei2-Labs/kompany-pro"
    assert pm.repo_slug("https://gitlab.com/x/y.git") is None
    monkeypatch.setattr(ws.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "https://github.com/Fei2-Labs/Kompany.git\n", ""))
    ok, reason, slug = pm.destination_allowed(tmp_path, "self-update/abc", pm.DEFAULT_ALLOWED_REPOS)
    assert ok and slug == "Fei2-Labs/Kompany"
    ok, reason, _ = pm.destination_allowed(tmp_path, "main", pm.DEFAULT_ALLOWED_REPOS)
    assert not ok and "proposal branch" in reason
    ok, reason, _ = pm.destination_allowed(tmp_path, "self-update/abc", ["Other/repo"])
    assert not ok and "allowlisted" in reason


def test_credential_loading_refuses_pat_and_honours_ambient_flag(tmp_path):
    f = tmp_path / "tok"
    f.write_text("ghp_personalaccesstoken\n")
    cred, why = pm.load_credential(ambient_ok=True, path=f)
    assert cred is None and "personal access token" in why
    f.write_text("ghs_appinstallationtoken\n")
    cred, why = pm.load_credential(ambient_ok=False, path=f)
    assert cred is not None and cred.kind == "app_token" and cred.fingerprint and "ghs_" not in why
    assert cred.http_extraheader.startswith("AUTHORIZATION: basic ")
    cred, why = pm.load_credential(ambient_ok=False, path=tmp_path / "none")
    assert cred is None and "disabled" in why
    cred, why = pm.load_credential(ambient_ok=True, path=tmp_path / "none")
    assert cred is not None and cred.kind == "ambient" and cred.token is None


def test_push_with_token_never_pushes_default_branch(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ws.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1])
    cred = pm.PromotionCredential("app_token", "ghs_x", "abc")
    ok, _ = pm.push_with_credential(tmp_path, "main", cred)
    assert not ok and calls == []
    ok, _ = pm.push_with_credential(tmp_path, "self-update/1", cred)
    assert ok and any("http.extraheader=AUTHORIZATION: basic" in c for c in calls[0]) and "credential.helper=" in calls[0]
    assert "ghs_x" not in " ".join(calls[0])  # token only travels base64-encoded in the header


def test_open_pull_request_uses_rest_with_app_token(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    seen = {}

    def post(url, **kw):
        seen.update(url=url, json=kw["json"], auth=kw["headers"]["Authorization"])
        return SimpleNamespace(status_code=201, json=lambda: {"html_url": "https://github.com/Fei2-Labs/Kompany/pull/9"})
    cred = pm.PromotionCredential("app_token", "ghs_x", "abc")
    url, detail = pm.open_pull_request(tmp_path, "Fei2-Labs/Kompany", "self-update/1", "t", "b", cred, post=post)
    assert url.endswith("/pull/9") and seen["url"].endswith("/repos/Fei2-Labs/Kompany/pulls")
    assert seen["json"]["head"] == "self-update/1" and seen["json"]["base"] == "main" and seen["auth"] == "Bearer ghs_x"


# ---------------------------------------------------------------------------
# approve effect end to end (fake git)
# ---------------------------------------------------------------------------

class FakeAudit:
    def __init__(self):
        self.events = []

    def record(self, event_type, description, **kw):
        self.events.append((event_type, kw.get("detail") or {}))


class FakeEngine:
    def __init__(self, tmp_path, **settings):
        self.db = Database(tmp_path / "db")
        self.approvals = ApprovalRequests(self.db)
        self.self_update_proposals = SelfUpdateProposalStore(self.db)
        self.audit = FakeAudit()
        self.settings = SimpleNamespace(data_dir=tmp_path / "data", **settings)


def _seed(engine, tmp_path, monkeypatch):
    clone = tmp_path / "data" / "self_update" / "repo"; clone.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("kompany.core.self_update.effects.ensure_clone", lambda d: clone)
    pid = engine.self_update_proposals.create("fix the thing")
    row = engine.self_update_proposals.get(pid)
    req = ApprovalRequest(action_type=ACTION_SELF_UPDATE, summary="x", requested_by="t",
                          payload={"proposal_id": pid, "branch": row["branch"], "tier": "t1", "instruction": "fix", "test_summary": "PASSED"})
    engine.approvals.create(req)
    return req


def _git_fake(calls, origin="https://github.com/Fei2-Labs/Kompany.git"):
    def run(cmd, **kw):
        calls.append(list(cmd))
        if "get-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, origin + "\n", "")
        if "format-patch" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "From abc\nSubject: [PATCH] fix\n", "")
        if cmd[0] == "gh":
            return subprocess.CompletedProcess(cmd, 0, "https://github.com/x/pr/1\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run


def _set_role(monkeypatch, role, trusted=True):
    r = ir.InstallationRole(role, "file", "/etc/kompany/installation_role", trusted, "test")
    monkeypatch.setattr("kompany.core.self_update.effects.resolve_installation_role", lambda: r)


def test_customer_role_exports_patch_and_never_pushes(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path); req = _seed(engine, tmp_path, monkeypatch)
    calls = []; monkeypatch.setattr(ws.subprocess, "run", _git_fake(calls))
    _set_role(monkeypatch, "customer")
    out = approve_self_update(engine, req)
    assert out["status"] == "patch_exported" and Path(out["patch_path"]).exists()
    assert not any("push" in c for c in calls) and not any(c[0] == "gh" for c in calls)
    fresh = engine.approvals.get(req.id)
    assert fresh.payload["effect_applied"] is True and fresh.payload["installation_role"] == "customer"
    kinds = [e for e, _ in engine.audit.events]
    assert "approval_effect.self_update_patch_exported" in kinds
    detail = dict(engine.audit.events)["approval_effect.self_update_patch_exported"]
    assert detail["installation_role"] == "customer" and detail["outcome"] == "patch_exported"


def test_untrusted_maintainer_file_is_treated_as_customer(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path); req = _seed(engine, tmp_path, monkeypatch)
    calls = []; monkeypatch.setattr(ws.subprocess, "run", _git_fake(calls))
    _set_role(monkeypatch, "customer", trusted=False)
    assert approve_self_update(engine, req)["status"] == "patch_exported"
    assert not any("push" in c for c in calls)


def test_maintainer_pushes_with_scoped_token_and_opens_pr(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path, self_update_ambient_credentials=False); req = _seed(engine, tmp_path, monkeypatch)
    calls = []; monkeypatch.setattr(ws.subprocess, "run", _git_fake(calls))
    _set_role(monkeypatch, "maintainer")
    tok = tmp_path / "promotion_token"; tok.write_text("ghs_scoped\n")
    monkeypatch.setenv(pm.PROMOTION_TOKEN_ENV, str(tok))
    monkeypatch.setattr(pm, "open_pull_request", lambda *a, **k: ("https://github.com/Fei2-Labs/Kompany/pull/7", "pr created"))
    monkeypatch.setattr("kompany.core.self_update.effects.open_pull_request", lambda *a, **k: ("https://github.com/Fei2-Labs/Kompany/pull/7", "pr created"))
    out = approve_self_update(engine, req)
    assert out["status"] == "pushed" and out["pr_url"].endswith("/pull/7")
    push = next(c for c in calls if "push" in c)
    assert push[-2:] == ["origin", req.payload["branch"]] and any("http.extraheader" in c for c in push)
    ev = dict(engine.audit.events)
    assert ev["approval_effect.self_update_credential"]["credential"] == "app_token"
    assert ev["approval_effect.self_update_pushed"]["repo"] == "Fei2-Labs/Kompany"
    assert "ghs_scoped" not in str(engine.audit.events)  # never logged
    assert engine.approvals.get(req.id).payload["credential"] == "app_token"


def test_maintainer_refused_when_origin_not_allowlisted(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path); req = _seed(engine, tmp_path, monkeypatch)
    calls = []; monkeypatch.setattr(ws.subprocess, "run", _git_fake(calls, origin="https://github.com/evil/fork.git"))
    _set_role(monkeypatch, "maintainer")
    out = approve_self_update(engine, req)
    assert out["status"] == "promotion_refused" and "allowlisted" in out["detail"]
    assert not any("push" in c for c in calls)
    assert not (engine.approvals.get(req.id).payload or {}).get("effect_applied")
    assert "approval_effect.self_update_promotion_refused" in dict(engine.audit.events)


def test_maintainer_refused_without_token_when_ambient_disabled(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path, self_update_ambient_credentials=False); req = _seed(engine, tmp_path, monkeypatch)
    calls = []; monkeypatch.setattr(ws.subprocess, "run", _git_fake(calls))
    _set_role(monkeypatch, "maintainer")
    monkeypatch.setenv(pm.PROMOTION_TOKEN_ENV, str(tmp_path / "absent"))
    out = approve_self_update(engine, req)
    assert out["status"] == "promotion_refused" and "disabled" in out["detail"]
    assert not any("push" in c for c in calls)


def test_role_surface_parity(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv(ir.ROLE_FILE_ENV, str(tmp_path / "absent"))
    from fastapi.testclient import TestClient
    from kompany.core.engine import KompanyEngine
    from kompany.interfaces import api
    e = KompanyEngine(); monkeypatch.setattr(api, "_engine", e)
    rest = TestClient(api.app).get("/self-update/role").json()
    assert rest["role"] == "customer" and rest["promotion"] == "patch_only" and "Fei2-Labs/Kompany" in rest["allowed_repos"]
    assert e.self_update_role() == rest
    from kompany.core.doctor import run_doctor
    n = next(c for c in run_doctor(e)["children"] if c["id"] == "installation_role")
    assert n["status"] == "info" and "role=customer" in n["detail"]
