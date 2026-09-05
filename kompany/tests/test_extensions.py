"""07-24 four-layer: customer extension layer (manifest, compat, worker, surfaces)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kompany.core.engine import KompanyEngine
from kompany.core.engine_parts import extensions as ext_ops
from kompany.core.extensions.manifest import ManifestError, core_compatible, load_manifest, package_hash
from kompany.core.extensions.worker import ExtensionHost, run_extension
from kompany.core.extensions.manifest import ExtensionManifest

ENTRY = '''
def run(job, host):
    out = {"echo": job.get("x")}
    if job.get("tool"):
        out["tool"] = host.tool(job["tool"], job.get("inputs") or {})
    if job.get("write"):
        host.write("notes/a.txt", "hello"); out["read"] = host.read("notes/a.txt")
    if job.get("bad_path"):
        try:
            host.read("../../kompany.db")
        except PermissionError as e:
            out["bad_path"] = str(e)
    if job.get("undeclared_tool"):
        try:
            host.tool("kompany.secret_tool", {})
        except PermissionError as e:
            out["undeclared_tool"] = str(e)
    if job.get("fetch"):
        try:
            host.fetch("https://evil.example.net/x")
        except PermissionError as e:
            out["fetch"] = str(e)
    if job.get("import_httpx"):
        try:
            import httpx  # noqa: F401
            out["import_httpx"] = "available"
        except ImportError:
            out["import_httpx"] = "blocked"
    if job.get("boom"):
        raise RuntimeError("boom")
    host.log("done")
    return out
'''


def _pkg(tmp_path: Path, **over) -> Path:
    d = tmp_path / "pkg"; d.mkdir(exist_ok=True)
    (d / "main.py").write_text(ENTRY)
    m = {"id": "acme.hello", "name": "Hello", "version": "1.0.0", "core_api": ">=0.1,<0.2",
         "capabilities": {"tools": [], "paths": ["notes/"], "network": ["api.example.com"], "budget_usd": 0}}
    m.update(over)
    (d / "extension.json").write_text(json.dumps(m))
    return d


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KOMPANY_INSTALLATION_ROLE_FILE", str(tmp_path / "norole"))
    return KompanyEngine()


# ---------------------------------------------------------------------------
# manifest + compat (pure)
# ---------------------------------------------------------------------------

def test_manifest_validation_and_hash(tmp_path):
    d = _pkg(tmp_path)
    m = load_manifest(d)
    assert m.id == "acme.hello" and m.capabilities.paths == ["notes/"]
    h = package_hash(d); assert len(h) == 64 and h == package_hash(d)
    (d / "main.py").write_text(ENTRY + "\n#"); assert package_hash(d) != h
    for bad in ({"id": "Bad Id"}, {"version": "x"}, {"entrypoint": "../x.py"}, {"core_api": "~=1"}, {"runtime": "node"},
                {"capabilities": {"paths": ["/etc"]}}):
        with pytest.raises(ManifestError):
            load_manifest(_pkg(tmp_path, **bad))
    (d / "main.py").unlink()
    with pytest.raises(ManifestError):
        load_manifest(_pkg(tmp_path, entrypoint="missing.py"))


def test_core_compatible_ranges():
    assert core_compatible(">=0.1,<0.2", "0.1.5")[0]
    ok, why = core_compatible(">=0.1,<0.2", "0.2.0"); assert not ok and "does not satisfy" in why
    assert core_compatible("", "9.9")[0]
    assert core_compatible(">=5", "0.0.0+unknown")[0]  # source checkout never blocks
    assert core_compatible("==0.1", "0.1.7")[0] and not core_compatible("!=0.1.7", "0.1.7")[0]


# ---------------------------------------------------------------------------
# install → card → approve → run (isolated worker)
# ---------------------------------------------------------------------------

def test_install_requires_approval_then_runs_isolated(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(ext_ops, "_core_version", lambda: "0.1.5")
    row = engine.extension_install(_pkg(tmp_path))
    assert row["status"] == "installed" and row["approval_id"]
    pkg = Path(row["pkg_path"]); assert (pkg / "extension.json").is_file() and "extensions/acme.hello/pkg/1.0.0" in row["pkg_path"]
    # not active yet → refuses to run, no worker
    out = engine.extension_run("acme.hello", {"x": 1})
    assert out["ok"] is False and "approve" in out["error"]
    card = engine.approvals.get(row["approval_id"])
    assert card.action_type == "extension_activate" and card.payload["capabilities"]["paths"] == ["notes/"]
    engine.approve_request(card.id)
    assert engine.extensions.get("acme.hello")["status"] == "active"
    assert engine.approvals.get(card.id).payload["effect_applied"] is True
    # idempotent replay
    assert ext_ops._approve_activation(engine, engine.approvals.get(card.id))["status"] == "already_applied"

    out = engine.extension_run("acme.hello", {"x": 7, "write": True, "bad_path": True, "undeclared_tool": True,
                                              "fetch": True, "import_httpx": True})
    assert out["ok"] is True, out
    r = out["result"]
    assert r["echo"] == 7 and r["read"] == "hello"
    assert "escapes" in r["bad_path"] or "not declared" in r["bad_path"] or "relative" in r["bad_path"]
    assert "not declared" in r["undeclared_tool"] and "not declared" in r["fetch"]
    assert r["import_httpx"] == "blocked"  # -I -S: no site-packages in the worker
    assert {d["op"] for d in out["denied"]} == {"read", "tool", "fetch"}
    assert (Path(engine.settings.data_dir) / "extensions" / "acme.hello" / "data" / "notes" / "a.txt").read_text() == "hello"
    kinds = [e["event_type"] for e in engine.audit.recent(limit=50)] if hasattr(engine.audit, "recent") else []
    shown = engine.extension_show("acme.hello")
    assert shown["runs"][0]["status"] == "ok" and len(shown["runs"][0]["denied"]) == 3
    # failures are reported, not swallowed
    bad = engine.extension_run("acme.hello", {"boom": True})
    assert bad["ok"] is False and "boom" in bad["error"]


def test_reject_disables_and_remove_keeps_files(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(ext_ops, "_core_version", lambda: "0.1.5")
    row = engine.extension_install(_pkg(tmp_path))
    engine.reject_request(row["approval_id"], reason="no")
    assert engine.extensions.get("acme.hello")["status"] == "disabled"
    removed = engine.extension_remove("acme.hello")
    assert removed["status"] == "removed" and Path(removed["pkg_path"]).is_dir()
    assert engine.extensions_list() == [] and engine.extensions.list(include_removed=True)


def test_declared_tool_call_goes_through_engine_gate(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(ext_ops, "_core_version", lambda: "0.1.5")
    from kompany.core import tool_actions
    from kompany.plugins.contract import CostEstimate, SideEffect, Tool

    class Ping(Tool):
        name = "test.ping"; side_effect = SideEffect.READ
        def estimate_cost(self, inputs): return CostEstimate()
        def execute(self, inputs, ctx): return {"pong": True}

    class Pay(Tool):
        name = "test.pay"; side_effect = SideEffect.SPEND
        def estimate_cost(self, inputs): return CostEstimate(external_usd=5.0)
        def execute(self, inputs, ctx): return {}

    registry = {"test.ping": {"tool": Ping()}, "test.pay": {"tool": Pay()}}
    monkeypatch.setattr(tool_actions, "tool_registry", lambda eng: registry)
    row = engine.extension_install(_pkg(tmp_path, capabilities={"tools": ["test.ping", "test.pay"], "paths": [], "network": [], "budget_usd": 1.0}))
    engine.approve_request(row["approval_id"])
    calls = []
    monkeypatch.setattr(engine, "execute_tool", lambda n, i: (calls.append(n), {"ok": True, "result": {"fine": True}})[1])
    out = engine.extension_run("acme.hello", {"tool": "test.ping"})
    assert out["ok"] and out["result"]["tool"] == {"fine": True} and calls == ["test.ping"]
    # a paid tool above the manifest budget is denied before reaching the engine
    out = engine.extension_run("acme.hello", {"tool": "test.pay"})
    assert out["ok"] is False and "budget" in out["error"] and calls == ["test.ping"]
    assert out["denied"][0]["op"] == "tool"


# ---------------------------------------------------------------------------
# Core update compatibility (plan step 4): block, never delete, self-unblock
# ---------------------------------------------------------------------------

def test_core_bump_blocks_extension_without_deleting_and_unblocks_later(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(ext_ops, "_core_version", lambda: "0.1.5")
    row = engine.extension_install(_pkg(tmp_path))
    engine.approve_request(row["approval_id"])
    assert engine.extensions.get("acme.hello")["status"] == "active"
    # simulated Core update → 0.2.0
    monkeypatch.setattr(ext_ops, "_core_version", lambda: "0.2.0")
    rep = engine.extensions_compat_check()
    blocked = engine.extensions.get("acme.hello")
    assert rep["blocked"] == ["acme.hello"] and blocked["status"] == "blocked" and blocked["status_before_block"] == "active"
    assert Path(blocked["pkg_path"]).is_dir()
    ev = engine.health_events.list(status="open", kind="extension_incompatible")
    assert len(ev) == 1 and ev[0]["detail"]["extension_id"] == "acme.hello"
    assert engine.extension_run("acme.hello", {})["ok"] is False
    # enable/disable cannot override the block
    assert engine.extension_set_enabled("acme.hello", True)["status"] == "blocked"
    from kompany.core.doctor import run_doctor
    n = next(c for c in run_doctor(engine)["children"] if c["id"] == "extensions")
    assert n["status"] == "warn" and "acme.hello" in n["fix"]
    # second sweep does not duplicate the event
    engine.extensions_compat_check()
    assert len(engine.health_events.list(status="open", kind="extension_incompatible")) == 1
    # Core rolled back / extension updated → unblocks itself, event resolves
    monkeypatch.setattr(ext_ops, "_core_version", lambda: "0.1.9")
    rep = engine.extensions_compat_check()
    assert rep["unblocked"] == ["acme.hello"] and engine.extensions.get("acme.hello")["status"] == "active"
    assert engine.health_events.list(status="open", kind="extension_incompatible") == []


def test_install_of_incompatible_extension_is_blocked_on_arrival(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(ext_ops, "_core_version", lambda: "3.0.0")
    row = engine.extension_install(_pkg(tmp_path))
    assert row["status"] == "blocked" and "core_api" in row["block_reason"]
    card = engine.approvals.get(row["approval_id"]); assert "BLOCKED" in card.summary
    engine.approve_request(card.id)  # approval recorded, activation deferred
    assert engine.extensions.get("acme.hello")["status"] == "blocked"


# ---------------------------------------------------------------------------
# backup / export round-trip (plan step 6)
# ---------------------------------------------------------------------------

def test_export_import_round_trips_extension_layer(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(ext_ops, "_core_version", lambda: "0.1.5")
    row = engine.extension_install(_pkg(tmp_path)); engine.approve_request(row["approval_id"])
    engine.extension_run("acme.hello", {"write": True})
    from kompany.state.export_bundle import create_bundle, import_bundle
    engine.db.close() if hasattr(engine.db, "close") else None
    out = create_bundle(engine.settings.data_dir, "pw", tmp_path / "c.kmp")
    assert any(f.startswith("extensions/acme.hello/pkg/1.0.0/") for f in out["files"])
    assert "extensions/acme.hello/data/notes/a.txt" in out["files"]
    dest = tmp_path / "restored"
    res = import_bundle(tmp_path / "c.kmp", "pw", dest)
    assert (dest / "extensions/acme.hello/pkg/1.0.0/extension.json").is_file()
    assert (dest / "extensions/acme.hello/data/notes/a.txt").read_text() == "hello"
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(dest))
    e2 = KompanyEngine()
    assert e2.extensions.get("acme.hello")["status"] == "active" and e2.extensions.runs("acme.hello")


def test_bundle_rejects_traversal_member_names():
    from kompany.state.export_bundle import _safe_extension_member
    assert _safe_extension_member("extensions/a/pkg/1/x.py") == Path("extensions/a/pkg/1/x.py")
    assert _safe_extension_member("extensions/../kompany.db") is None
    assert _safe_extension_member("other/x") is None


# ---------------------------------------------------------------------------
# four surfaces
# ---------------------------------------------------------------------------

def test_surfaces_parity(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(ext_ops, "_core_version", lambda: "0.1.5")
    from fastapi.testclient import TestClient
    from kompany.interfaces import api
    from kompany.interfaces.mcp_dispatch import dispatch_tool
    from kompany.interfaces.mcp_server import TOOLS
    from kompany.interfaces.sdk import Kompany
    monkeypatch.setattr(api, "_engine", engine)
    c = TestClient(api.app)
    row = c.post("/extensions/install", json={"path": str(_pkg(tmp_path))}).json()
    assert row["id"] == "acme.hello" and c.get("/extensions").json()[0]["id"] == "acme.hello"
    assert c.get("/extensions/acme.hello").json()["status"] == "installed"
    assert c.post("/extensions/install", json={"path": str(tmp_path / "nope")}).status_code == 422
    assert dispatch_tool(engine, "kompany_extensions_list", {}) == engine.extensions_list()
    assert dispatch_tool(engine, "kompany_extension_show", {"extension_id": "acme.hello"})["status"] == "installed"
    assert {t.name for t in TOOLS} >= {"kompany_extensions_list", "kompany_extension_show", "kompany_extension_install",
                                       "kompany_extension_run", "kompany_extension_set_enabled"}
    sdk = Kompany.__new__(Kompany); sdk._engine = engine
    assert sdk.extensions_list() == engine.extensions_list()
    engine.approve_request(row["approval_id"])
    run = c.post("/extensions/acme.hello/run", json={"job": {"x": 2}}).json()
    assert run["ok"] and run["result"]["echo"] == 2
    assert c.post("/extensions/acme.hello/enabled", json={"enabled": False}).json()["status"] == "disabled"
    assert c.delete("/extensions/acme.hello").json()["status"] == "removed"
    from typer.testing import CliRunner
    from kompany.interfaces.cli import app
    res = CliRunner().invoke(app, ["extensions", "list", "--json"])
    assert res.exit_code == 0
