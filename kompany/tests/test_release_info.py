"""Stage C: release identity of the running engine + deployment drift."""

from __future__ import annotations

import json
from pathlib import Path

from kompany.core import release_info as ri
from kompany.core.engine import KompanyEngine

GH = {"source": "github-actions", "version": "0.2.0", "commit": "a" * 40, "tag": "v0.2.0",
      "built_at": "2026-09-05T00:00:00Z", "repository": "Fei2-Labs/Kompany", "run_url": "https://x/run/1"}


def test_identity_from_packaged_release_file():
    ident = ri.release_identity(GH, None)
    assert ident["source"] == "github-release" and ident["commit"] == "a" * 40
    assert ident["release_tag"] == "v0.2.0" and ident["run_url"] == "https://x/run/1"


def test_identity_checkout_vs_local_build(tmp_path):
    assert ri.release_identity(None, None)["source"] == "local-build"
    ident = ri.release_identity(None, Path(__file__).resolve().parents[2])
    assert ident["source"] == "source-checkout" and ident["commit"] != "unknown"


def test_release_run_records_identity_then_checkout_drifts(tmp_path):
    gh = ri.release_identity(GH, None)
    first = ri.sync_deployment_identity(tmp_path, gh)
    assert first["drift"] is False
    stored = json.loads((tmp_path / ri.IDENTITY_FILE).read_text())
    assert stored["last_github_release"]["release_tag"] == "v0.2.0"
    assert oct((tmp_path / ri.IDENTITY_FILE).stat().st_mode & 0o777) == "0o600"
    dev = ri.release_identity(None, None)
    drift = ri.sync_deployment_identity(tmp_path, dev)
    assert drift["drift"] is True and drift["expected"]["commit"] == "a" * 40
    assert "v0.2.0" in drift["hint"] and "local-build" in drift["hint"] and ri.IDENTITY_FILE in drift["hint"]
    # a release running again clears it (and re-records the newer release)
    newer = ri.release_identity({**GH, "version": "0.2.1", "tag": "v0.2.1", "commit": "b" * 40}, None)
    again = ri.sync_deployment_identity(tmp_path, newer)
    assert again["drift"] is False and again["expected"]["release_tag"] == "v0.2.1"


def test_dev_machine_never_drifts(tmp_path):
    assert ri.sync_deployment_identity(tmp_path, ri.release_identity(None, None))["drift"] is False
    assert not (tmp_path / ri.IDENTITY_FILE).exists()


def test_corrupt_identity_file_is_treated_as_absent(tmp_path):
    (tmp_path / ri.IDENTITY_FILE).write_text("{not json")
    assert ri.sync_deployment_identity(tmp_path, ri.release_identity(None, None))["drift"] is False


def test_engine_boot_files_one_drift_event_and_resolves_it(tmp_path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(data))
    # pretend a GitHub release ran here earlier
    ri.sync_deployment_identity(data, ri.release_identity(GH, None))
    e = KompanyEngine()
    assert e.deployment_drift["drift"] is True
    events = e.health_events.list(status="open", kind=ri.KIND_DEPLOYMENT_DRIFT)
    assert len(events) == 1 and events[0]["detail"]["expected"]["release_tag"] == "v0.2.0"
    # second boot on the same data dir does not duplicate
    e2 = KompanyEngine()
    assert len(e2.health_events.list(status="open", kind=ri.KIND_DEPLOYMENT_DRIFT)) == 1
    # doctor fails on drift; /version and /status carry release + drift
    from kompany.core.doctor import run_doctor
    build = next(n for n in run_doctor(e2)["children"] if n["id"] == "build")
    assert build["status"] == "fail" and "Reinstall" in build["fix"]
    from fastapi.testclient import TestClient
    from kompany.interfaces import api
    monkeypatch.setattr(api, "_engine", e2)
    v = TestClient(api.app).get("/version").json()
    assert v["release"]["source"] in ("source-checkout", "local-build") and v["drift"]["drift"] is True
    # the release comes back: drift event auto-resolves
    monkeypatch.setattr(ri, "_read_packaged_release", lambda: GH)
    e3 = KompanyEngine()
    assert e3.deployment_drift["drift"] is False
    assert e3.health_events.list(status="open", kind=ri.KIND_DEPLOYMENT_DRIFT) == []
    assert e3.health_events.list(status="resolved", kind=ri.KIND_DEPLOYMENT_DRIFT)


def test_version_without_drift_on_fresh_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "d"))
    from fastapi.testclient import TestClient
    from kompany.interfaces import api
    api.reset_engine()
    v = TestClient(api.app).get("/version").json()
    assert v["drift"] == {"drift": False, "expected": None} or v["drift"]["drift"] is False
    assert "source" in v["release"]
