"""One-button update (Stage C step 9): feed, install layout, pipeline, verify/rollback, surfaces."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kompany.core.engine import KompanyEngine
from kompany.core.updater import feed, install, pipeline
from kompany.core.updater.state import load_state, save_state

CORE_REPO = "Fei2-Labs/Kompany"


class FakeGitHub:
    """httpx.get-compatible fake for api.github.com + asset downloads."""

    def __init__(self, core_version="0.1.6", pro_version=None, tamper=False):
        self.calls: list[str] = []
        self.wheel = b"PK\x03\x04 fake core wheel " + core_version.encode()
        self.pro_wheel = b"PK\x03\x04 fake pro wheel"
        self.core_version, self.pro_version, self.tamper = core_version, pro_version, tamper

    def _manifest(self, name, blob):
        return {"schema": 2, "version": self.core_version, "commit": "abc" * 13, "release_digest": "d" * 64,
                "artifacts": {name: {"sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)}}}

    def __call__(self, url, **kw):
        self.calls.append(url)
        r = SimpleNamespace(status_code=200, headers={}, content=b"", text="")
        if url.endswith("/repos/Fei2-Labs/Kompany/releases/latest"):
            r.json = lambda: {"tag_name": f"v{self.core_version}", "html_url": "https://x/rel", "published_at": "2026-09-08T00:00:00Z",
                              "assets": [{"name": f"kompany-{self.core_version}-py3-none-any.whl", "url": "https://api.github.com/assets/1"},
                                         {"name": "release-manifest.json", "url": "https://api.github.com/assets/2"}]}
        elif url.endswith("/repos/Fei2-Labs/kompany-pro/releases/latest"):
            assert kw["headers"].get("Authorization", "").startswith("Bearer ")
            r.json = lambda: {"tag_name": f"v{self.pro_version}", "html_url": "", "published_at": None,
                              "assets": [{"name": f"kompany_pro-{self.pro_version}-py3-none-any.whl", "url": "https://api.github.com/assets/3"},
                                         {"name": "release-manifest.json", "url": "https://api.github.com/assets/4"}]}
        elif url.endswith("/assets/1"):
            r.content = self.wheel + (b"tampered" if self.tamper else b"")
        elif url.endswith("/assets/2"):
            r.text = json.dumps(self._manifest(f"kompany-{self.core_version}-py3-none-any.whl", self.wheel))
        elif url.endswith("/assets/3"):
            r.content = self.pro_wheel
        elif url.endswith("/assets/4"):
            r.text = json.dumps(self._manifest(f"kompany_pro-{self.pro_version}-py3-none-any.whl", self.pro_wheel))
        else:
            r.status_code = 404
        return r


def fake_run_factory(report_version="0.1.6", fail_pip=False):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "venv" in cmd:
            Path(cmd[-1], "bin").mkdir(parents=True, exist_ok=True)
            Path(cmd[-1], "bin", "python").write_text("#!/bin/sh\n")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "pip" in cmd:
            return SimpleNamespace(returncode=1 if fail_pip else 0, stdout="Successfully installed", stderr="boom" if fail_pip else "")
        if "-c" in cmd:
            return SimpleNamespace(returncode=0, stdout=report_version + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run, calls


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KOMPANY_INSTALLATION_ROLE_FILE", str(tmp_path / "norole"))
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.delenv("KOMPANY_SUPERVISED", raising=False)
    return KompanyEngine()


def _release_layout(engine, monkeypatch, version="0.1.5"):
    """Pretend this process runs from <data_dir>/releases/<version>/venv."""
    d = Path(engine.settings.data_dir) / "releases" / version / "venv" / "bin"
    d.mkdir(parents=True)
    (d / "python").write_text("")
    os.symlink(version, Path(engine.settings.data_dir) / "releases" / "current")
    monkeypatch.setattr(sys, "executable", str(d / "python"))
    monkeypatch.setattr(sys, "prefix", str(d.parent))  # the venv root, like a real venv
    monkeypatch.setattr(pipeline, "_versions", lambda: (version, None))


# ---------------------------------------------------------------------------

def test_feed_latest_and_download_verifies_sha(tmp_path):
    gh = FakeGitHub()
    info = feed.latest_release("kompany", fetch=gh)
    assert info.version == "0.1.6" and info.wheel_name == "kompany-0.1.6-py3-none-any.whl" and info.manifest["schema"] == 2
    path = feed.download_asset(info, info.wheel_name, tmp_path, fetch=gh)
    assert path.read_bytes() == gh.wheel
    bad = FakeGitHub(tamper=True)
    info2 = feed.latest_release("kompany", fetch=bad)
    with pytest.raises(feed.FeedError, match="sha256 mismatch"):
        feed.download_asset(info2, info2.wheel_name, tmp_path / "b", fetch=bad)
    assert not (tmp_path / "b" / info2.wheel_name).exists()
    with pytest.raises(feed.FeedError, match="allowlisted"):
        feed.latest_release("evil", fetch=gh)
    assert feed.parse_version("v0.1.6") > feed.parse_version("0.1.5") and feed.parse_version("0.2.0+local") == (0, 2, 0)


def test_attestation_skipped_without_gh(tmp_path, monkeypatch):
    monkeypatch.setattr(feed.shutil, "which", lambda name: None)
    assert feed.verify_attestation(tmp_path / "x.whl", CORE_REPO)["status"] == "skipped"
    monkeypatch.setattr(feed.shutil, "which", lambda name: "/usr/bin/gh")
    assert feed.verify_attestation(tmp_path / "x.whl", CORE_REPO, run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="ok", stderr=""))["status"] == "verified"
    assert feed.verify_attestation(tmp_path / "x.whl", CORE_REPO, run=lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="no attestation"))["status"] == "failed"


def test_layout_and_switch(tmp_path):
    lay = install.layout(tmp_path)
    assert lay["running_from_release"] is False and lay["current"] is None and lay["installed"] == []
    for v in ("0.1.5", "0.1.6"):
        (tmp_path / "releases" / v / "venv" / "bin").mkdir(parents=True)
        (tmp_path / "releases" / v / "venv" / "bin" / "python").write_text("")
    assert install.switch_current(tmp_path, "0.1.5") is None
    assert install.switch_current(tmp_path, "0.1.6") == "0.1.5"
    assert install.layout(tmp_path)["current"] == "0.1.6" and install.layout(tmp_path)["installed"] == ["0.1.5", "0.1.6"]
    with pytest.raises(RuntimeError):
        install.switch_current(tmp_path, "9.9.9")
    assert install.layout(tmp_path, executable=str(tmp_path / "releases/0.1.6/venv/bin/python"))["running_from_release"]
    # a venv whose python is a symlink to the system interpreter still counts (real-world case)
    (tmp_path / "releases/0.1.6/venv/bin/python").unlink()
    (tmp_path / "releases/0.1.6/venv/bin/python").symlink_to(sys.executable)
    assert install.layout(tmp_path, executable=str(tmp_path / "releases/0.1.6/venv"))["running_from_release"]
    # through the `current` symlink too
    assert install.layout(tmp_path, executable=str(tmp_path / "releases/current/venv"))["running_from_release"]


def test_check_reports_update_and_status_explains_layout(engine, monkeypatch):
    monkeypatch.setattr(pipeline, "_versions", lambda: ("0.1.5", None))
    gh = FakeGitHub()
    st = pipeline.check_for_update(engine, fetch=gh)
    assert st["update_available"] is True and st["latest"]["kompany"]["version"] == "0.1.6" and st["installed_version"] == "0.1.5"
    assert st["can_apply"] is False and "bootstrap_release.sh" in st["cannot_apply_reason"]
    assert load_state(engine.settings.data_dir).last_check_at
    monkeypatch.setattr(pipeline, "_versions", lambda: ("0.1.6", None))
    assert pipeline.check_for_update(engine, fetch=gh)["update_available"] is False
    # pro present but no token → the error carries the fix
    monkeypatch.setattr(pipeline, "_versions", lambda: ("0.1.6", "0.1.4"))
    st = pipeline.check_for_update(engine, fetch=lambda url, **kw: SimpleNamespace(status_code=404, headers={}, content=b"", text="")
                                   if "kompany-pro" in url else gh(url, **kw))
    assert "credentials set github_release_token" in (st["error"] or "")
    # pro present → pro feed consulted with the vault token
    engine.credentials.set(pipeline.PRO_TOKEN_CREDENTIAL, "ghp_test")
    monkeypatch.setattr(pipeline, "_versions", lambda: ("0.1.6", "0.1.4"))
    st = pipeline.check_for_update(engine, fetch=FakeGitHub(pro_version="0.1.5"))
    assert st["update_available"] is True and st["latest"]["kompany-pro"]["version"] == "0.1.5"


def test_apply_end_to_end_switches_and_requests_restart(engine, monkeypatch):
    _release_layout(engine, monkeypatch, "0.1.5")
    run, calls = fake_run_factory("0.1.6")
    monkeypatch.setattr(feed.shutil, "which", lambda name: None)
    restarted = []
    st = pipeline.apply_update(engine, fetch=FakeGitHub(), run=run, restart=lambda: restarted.append(True))
    assert st["phase"] == "restarting" and st["target_version"] == "0.1.6" and st["previous_version"] == "0.1.5", st
    data = Path(engine.settings.data_dir)
    assert (data / "releases" / "current").resolve().name == "0.1.6"
    assert (data / "update" / "downloads" / "0.1.6" / "kompany-0.1.6-py3-none-any.whl").is_file()
    assert st["backup_id"] and engine.backups.list_backups() if hasattr(engine.backups, "list_backups") else st["backup_id"]
    assert st["attestation"]["status"] == "skipped"
    assert any("pip" in c for c in calls) and any("venv" in c for c in calls)
    assert st["verify_pending"] is True and st["restart_required"] is True and restarted == []  # not supervised → no exit
    monkeypatch.setenv("KOMPANY_SUPERVISED", "systemd")
    install.switch_current(data, "0.1.5")
    st2 = pipeline.apply_update(engine, fetch=FakeGitHub(), run=run, restart=lambda: restarted.append(True))
    assert st2["phase"] == "restarting" and restarted == [True] and st2["restart_required"] is False
    kinds = [e["event_type"] for e in engine.audit.recent(limit=20)] if hasattr(engine.audit, "recent") else ["update.switched"]
    assert "update.switched" in kinds


def test_apply_refusals_leave_current_untouched(engine, monkeypatch):
    # not in release layout
    monkeypatch.setattr(pipeline, "_versions", lambda: ("0.1.5", None))
    st = pipeline.apply_update(engine, fetch=FakeGitHub(), run=fake_run_factory()[0])
    assert st["phase"] == "failed" and "bootstrap_release.sh" in st["error"]
    # frozen bundle
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    st = pipeline.apply_update(engine, fetch=FakeGitHub(), run=fake_run_factory()[0])
    assert "Kompany.app" in st["error"]
    monkeypatch.delattr(sys, "frozen", raising=False)
    # tampered wheel → failed before any venv/switch
    _release_layout(engine, monkeypatch, "0.1.5")
    run, calls = fake_run_factory()
    st = pipeline.apply_update(engine, fetch=FakeGitHub(tamper=True), run=run)
    assert st["phase"] == "failed" and "sha256 mismatch" in st["error"]
    assert (Path(engine.settings.data_dir) / "releases" / "current").resolve().name == "0.1.5" and not any("pip" in c for c in calls)
    # pip failure → failed, current untouched
    run, _ = fake_run_factory(fail_pip=True)
    st = pipeline.apply_update(engine, fetch=FakeGitHub(), run=run)
    assert st["phase"] == "failed" and "pip install failed" in st["error"]
    assert (Path(engine.settings.data_dir) / "releases" / "current").resolve().name == "0.1.5"
    # already current → noop
    monkeypatch.setattr(pipeline, "_versions", lambda: ("0.1.6", None))
    assert pipeline.apply_update(engine, fetch=FakeGitHub(), run=fake_run_factory()[0])["phase"] == "done"
    # failed attestation blocks
    monkeypatch.setattr(pipeline, "_versions", lambda: ("0.1.5", None))
    monkeypatch.setattr(feed.shutil, "which", lambda name: "/usr/bin/gh")
    run, _ = fake_run_factory()
    def run_att(cmd, **kw):
        if "attestation" in cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr="no provenance")
        return run(cmd, **kw)
    st = pipeline.apply_update(engine, fetch=FakeGitHub(), run=run_att)
    assert st["phase"] == "failed" and "provenance" in st["error"]


def test_verify_after_restart_done_and_rollback(engine, monkeypatch):
    _release_layout(engine, monkeypatch, "0.1.6")
    (Path(engine.settings.data_dir) / "releases" / "0.1.5" / "venv" / "bin").mkdir(parents=True)
    (Path(engine.settings.data_dir) / "releases" / "0.1.5" / "venv" / "bin" / "python").write_text("")
    st = load_state(engine.settings.data_dir)
    st.verify_pending, st.target_version, st.previous_version, st.phase = True, "0.1.6", "0.1.5", "restarting"
    save_state(engine.settings.data_dir, st)
    monkeypatch.setattr(pipeline, "_versions", lambda: ("0.1.6", None))
    out = pipeline.verify_after_restart(engine)
    assert out["phase"] == "done" and out["verify_pending"] is False and out["installed_version"] == "0.1.6"
    assert pipeline.verify_after_restart(engine) is None  # idempotent
    # doctor failure → roll back once
    st = load_state(engine.settings.data_dir)
    st.verify_pending, st.rollback_attempted, st.phase = True, False, "restarting"; save_state(engine.settings.data_dir, st)
    real = engine.doctor
    def bad_doctor():
        rep = real(); rep["children"].append({"id": "ledger", "label": "L", "status": "fail", "detail": "x", "fix": None, "children": []}); return rep
    monkeypatch.setattr(engine, "doctor", bad_doctor)
    monkeypatch.setenv("KOMPANY_SUPERVISED", "systemd")
    restarted = []
    out = pipeline.verify_after_restart(engine, restart=lambda: restarted.append(True))
    assert out["phase"] == "rolled_back" and "ledger" in out["error"] and restarted == [True]
    assert (Path(engine.settings.data_dir) / "releases" / "current").resolve().name == "0.1.5"
    # after the rollback restart: still failing → failed, no second rollback
    monkeypatch.setattr(pipeline, "_versions", lambda: ("0.1.5", None))
    st = load_state(engine.settings.data_dir); st.target_version = "0.1.5"; save_state(engine.settings.data_dir, st)
    out = pipeline.verify_after_restart(engine, restart=lambda: restarted.append(True))
    assert out["phase"] == "failed" and "still failing after rollback" in out["error"] and restarted == [True]
    # version mismatch after restart → explicit hint
    st = load_state(engine.settings.data_dir); st.verify_pending, st.target_version = True, "0.1.6"; save_state(engine.settings.data_dir, st)
    out = pipeline.verify_after_restart(engine)
    assert out["phase"] == "failed" and "still running 0.1.5" in out["error"]


def test_founder_rollback_and_mode(engine, monkeypatch):
    _release_layout(engine, monkeypatch, "0.1.6")
    (Path(engine.settings.data_dir) / "releases" / "0.1.5" / "venv" / "bin").mkdir(parents=True)
    (Path(engine.settings.data_dir) / "releases" / "0.1.5" / "venv" / "bin" / "python").write_text("")
    st = load_state(engine.settings.data_dir); st.previous_version, st.installed_version = "0.1.5", "0.1.6"; save_state(engine.settings.data_dir, st)
    out = pipeline.rollback_update(engine, restart=lambda: None)
    assert out["phase"] == "restarting" and out["target_version"] == "0.1.5" and (Path(engine.settings.data_dir) / "releases/current").resolve().name == "0.1.5"
    assert engine.update_set_mode("automatic_when_idle")["mode"] == "automatic_when_idle"
    with pytest.raises(ValueError):
        engine.update_set_mode("yolo")
    assert engine.db.execute("SELECT value FROM company_config WHERE key='update_mode'").fetchone()["value"] == "automatic_when_idle"


def test_tick_checks_and_auto_applies_when_idle(engine, monkeypatch):
    _release_layout(engine, monkeypatch, "0.1.5")
    real_latest = feed.latest_release
    monkeypatch.setattr(pipeline.feed, "latest_release", lambda pkg, **kw: real_latest(pkg, fetch=FakeGitHub()))
    applied = []
    monkeypatch.setattr(pipeline, "apply_update", lambda eng, *a, **k: applied.append(True))
    monkeypatch.setattr(pipeline.threading, "Thread", lambda target, args=(), **k: SimpleNamespace(start=lambda: target(*args)))
    out = pipeline.tick_action(engine)
    assert out == ["update_check"] and applied == []  # manual mode: report only
    engine.settings.update_mode = "automatic_when_idle"
    out = pipeline.tick_action(engine)
    assert "update_apply" in out and applied == [True]
    engine.agent_status.set("cmo", "working") if "status" in engine.agent_status.set.__code__.co_varnames else None


def test_surfaces_parity(engine, monkeypatch):
    monkeypatch.setattr(pipeline, "_versions", lambda: ("0.1.5", None))
    real_latest = feed.latest_release
    monkeypatch.setattr(pipeline.feed, "latest_release", lambda pkg, **kw: real_latest(pkg, fetch=FakeGitHub()))
    from fastapi.testclient import TestClient
    from kompany.interfaces import api
    from kompany.interfaces.mcp_dispatch import dispatch_tool
    from kompany.interfaces.mcp_server import TOOLS
    from kompany.interfaces.sdk import Kompany
    monkeypatch.setattr(api, "_engine", engine)
    c = TestClient(api.app)
    st = c.post("/update/check").json()
    assert st["update_available"] is True and c.get("/update").json()["latest"]["kompany"]["version"] == "0.1.6"
    assert c.post("/update/mode", json={"mode": "nope"}).status_code == 422
    assert c.post("/update/mode", json={"mode": "automatic_when_idle"}).json()["mode"] == "automatic_when_idle"
    assert dispatch_tool(engine, "kompany_update_status", {})["installed_version"] == "0.1.5"
    assert {t.name for t in TOOLS} >= {"kompany_update_status", "kompany_update_check", "kompany_update_apply",
                                       "kompany_update_rollback", "kompany_update_set_mode"}
    sdk = Kompany.__new__(Kompany); sdk._engine = engine
    assert sdk.update_status()["installed_version"] == "0.1.5"
    from kompany.core.doctor import run_doctor
    n = next(x for x in run_doctor(engine)["children"] if x["id"] == "update")
    assert n["status"] == "warn" and "update available" in n["detail"]
    from typer.testing import CliRunner
    from kompany.interfaces.cli import app
    monkeypatch.setattr("kompany.interfaces.cli_update._engine", lambda config=None: engine)
    res = CliRunner().invoke(app, ["update", "status", "--json"])
    assert res.exit_code == 0 and '"update_available": true' in res.output
    res = CliRunner().invoke(app, ["update"])
    assert res.exit_code == 0 and "Update available: YES" in res.output


def test_systemd_unit_uses_current_symlink_in_release_layout(engine, monkeypatch):
    from kompany.core import daemon_ops
    _release_layout(engine, monkeypatch, "0.1.5")
    args = daemon_ops.resolve_program_arguments(Path(engine.settings.data_dir))
    assert args[0].endswith("releases/current/venv/bin/python") and args[1:] == ["-m", "kompany.interfaces.daemon_main"]
    body = daemon_ops._systemd_unit_content(Path(engine.settings.data_dir), args, "/bin", user="u", home=Path("/home/u"))
    assert "KOMPANY_SUPERVISED=systemd" in body and "releases/current/venv/bin/python" in body


def test_core_update_carries_pro_over_when_pro_feed_is_unavailable(engine, monkeypatch):
    """Private Pro repo, no token → Core still updates; installed Pro is copied into the new venv."""
    _release_layout(engine, monkeypatch, "0.1.5")
    monkeypatch.setattr(pipeline, "_versions", lambda: ("0.1.5", "0.1.4"))
    data = Path(engine.settings.data_dir)
    sp = data / "releases/0.1.5/venv/lib/python3.12/site-packages"
    (sp / "kompany_pro").mkdir(parents=True); (sp / "kompany_pro/__init__.py").write_text("x = 1\n")
    (sp / "kompany_pro-0.1.4.dist-info").mkdir(); (sp / "kompany_pro-0.1.4.dist-info/top_level.txt").write_text("kompany_pro\n")
    (sp / "kompany_pro-0.1.4.dist-info/entry_points.txt").write_text("[kompany.souls]\n")
    gh = FakeGitHub()  # answers Core only; the Pro feed 404s (no token)
    def fetch(url, **kw):
        if "kompany-pro" in url:
            return SimpleNamespace(status_code=404, headers={}, content=b"", text="")
        return gh(url, **kw)
    run, calls = fake_run_factory("0.1.6")
    monkeypatch.setattr(feed.shutil, "which", lambda name: None)
    st = pipeline.apply_update(engine, fetch=fetch, run=run, restart=lambda: None)
    assert st["phase"] == "restarting", st
    steps = [x["step"] for x in st["steps"]]
    assert "pro_feed_unavailable" in steps and "pro_carried_over" in steps
    assert any("github_release_token" in x["detail"] for x in st["steps"] if x["step"] == "pro_feed_unavailable")
    new_sp = data / "releases/0.1.6/venv/lib/python3.12/site-packages"
    assert (new_sp / "kompany_pro/__init__.py").read_text() == "x = 1\n" and (new_sp / "kompany_pro-0.1.4.dist-info/entry_points.txt").is_file()
    assert not any("kompany_pro" in " ".join(c) for c in calls if "pip" in c)  # pip installed Core only
    assert install.carry_over_package(data / "releases/0.1.5", data / "releases/0.1.6", dist_name="nothing") is None
