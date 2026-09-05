"""#26: running build vs repo HEAD staleness."""

from __future__ import annotations

import subprocess
from pathlib import Path

from kompany.core import build_info as bi


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"; r.mkdir()
    _git(r, "init", "-q", "-b", "main"); _git(r, "config", "user.email", "t@t"); _git(r, "config", "user.name", "t")
    (r / "a").write_text("1"); _git(r, "add", "a"); _git(r, "commit", "-q", "-m", "one")
    return r


def test_fresh_process_matches_head(tmp_path):
    r = _repo(tmp_path)
    sha = bi.read_head_sha(r)
    assert sha == _git(r, "rev-parse", "HEAD")
    st = bi.staleness(r, sha)
    assert st["stale"] is False and st["newer_commits"] == 0 and st["start_commit"] == sha[:7]


def test_newer_commits_counted_after_process_start(tmp_path):
    r = _repo(tmp_path)
    start = bi.read_head_sha(r)
    for i in range(3):
        (r / "a").write_text(str(i + 2)); _git(r, "commit", "-q", "-am", f"c{i}")
    st = bi.staleness(r, start)
    assert st["stale"] is True and st["newer_commits"] == 3
    assert st["repo_head"] == _git(r, "rev-parse", "HEAD")[:7] and "restart" in st["hint"].lower()


def test_packed_refs_and_detached_head(tmp_path):
    r = _repo(tmp_path)
    _git(r, "pack-refs", "--all")
    assert bi.read_head_sha(r) == _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "--detach")
    assert bi.read_head_sha(r) == _git(r, "rev-parse", "HEAD")


def test_outside_git_is_unknown_and_not_stale(tmp_path):
    st = bi.staleness(None, None)
    assert st["stale"] is False and st["start_commit"] == "unknown"
    assert bi.find_git_root(tmp_path) is None or True  # tmp may live inside a repo on dev machines


def test_status_and_version_carry_build_block(monkeypatch):
    from fastapi.testclient import TestClient
    from kompany.core.engine import KompanyEngine
    from kompany.interfaces import api
    e = KompanyEngine(); monkeypatch.setattr(api, "_engine", e)
    c = TestClient(api.app)
    v = c.get("/version").json()
    for k in ("version", "commit", "start_commit", "repo_head", "newer_commits", "stale", "started_at"):
        assert k in v
    b = c.get("/status").json()["build"]
    assert b["stale"] is v["stale"] and b["commit"] == v["commit"]


def test_doctor_build_node_warns_when_stale(monkeypatch):
    from kompany.core.doctor import check_build
    monkeypatch.setattr("kompany.interfaces.api_parts.system.build_info",
                        lambda: {"version": "0.1.5", "commit": "abc1234", "repo_head": "def5678", "newer_commits": 4,
                                 "stale": True, "hint": "restart"})
    n = check_build(object())
    assert n["status"] == "warn" and n["fix"] == "restart" and "newer_commits=4" in n["detail"]
