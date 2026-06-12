"""Clone manager tests against throwaway local git repos (PRD D1)."""

from __future__ import annotations

import subprocess

import pytest

from kompany.core.self_update.workspace import (
    commit_all,
    default_branch,
    diff_stats,
    discard_branch,
    ensure_clone,
    start_branch,
)

_IDENTITY = [
    "-c",
    "user.name=test",
    "-c",
    "user.email=test@localhost",
    "-c",
    "commit.gpgsign=false",
]


def _git(repo, *args):
    proc = subprocess.run(
        ["git", "-C", str(repo), *_IDENTITY, *args],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _make_origin(tmp_path, branch="main"):
    """A local 'origin' repo with one commit on ``branch``."""
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "-b", branch, str(origin)],
        capture_output=True,
        text=True,
        check=True,
    )
    (origin / "README.md").write_text("# fake kompany\n")
    (origin / "docs").mkdir()
    (origin / "docs" / "guide.md").write_text("guide\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "--quiet", "-m", "initial")
    return origin


@pytest.fixture()
def origin(tmp_path):
    return _make_origin(tmp_path)


def test_ensure_clone_from_local_source(tmp_path, origin):
    data_dir = tmp_path / "data"
    clone = ensure_clone(data_dir, source_hint=origin)
    assert clone == data_dir / "self_update" / "repo"
    assert (clone / ".git").exists()
    assert (clone / "README.md").read_text() == "# fake kompany\n"


def test_ensure_clone_is_idempotent_and_fetches(tmp_path, origin):
    data_dir = tmp_path / "data"
    first = ensure_clone(data_dir, source_hint=origin)
    # New commit upstream; re-ensure must fetch (best-effort) and reuse.
    (origin / "new.md").write_text("new\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "--quiet", "-m", "second")
    second = ensure_clone(data_dir, source_hint=origin)
    assert second == first
    log = _git(first, "log", "--oneline", "origin/main")
    assert "second" in log


def test_ensure_clone_total_failure_raises(tmp_path):
    with pytest.raises(RuntimeError, match="clone failed"):
        ensure_clone(tmp_path / "data", source_hint=tmp_path / "nonexistent")


def test_branch_commit_diff_discard_roundtrip(tmp_path, origin):
    clone = ensure_clone(tmp_path / "data", source_hint=origin)
    start_branch(clone, "self-update/abc123")
    head = _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert head == "self-update/abc123"

    # No changes yet → nothing to commit, empty diff.
    assert commit_all(clone, "noop") is False
    files, stat = diff_stats(clone)
    assert files == []
    assert stat == ""

    (clone / "docs" / "new.md").write_text("hello\n")
    (clone / "README.md").write_text("# changed\n")
    assert commit_all(clone, "Self-update: change docs") is True
    files, stat = diff_stats(clone)
    assert files == ["README.md", "docs/new.md"]
    assert "2 files changed" in stat

    # Uncommitted working-tree changes are also part of the evidence.
    (clone / "docs" / "extra.md").write_text("extra\n")
    files, _ = diff_stats(clone)
    assert "docs/extra.md" in files

    discard_branch(clone, "self-update/abc123")
    head = _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert head == "main"
    branches = _git(clone, "branch", "--list", "self-update/abc123")
    assert branches.strip() == ""


def test_master_named_repo_fallback(tmp_path):
    origin = _make_origin(tmp_path, branch="master")
    clone = ensure_clone(tmp_path / "data", source_hint=origin)
    assert default_branch(clone) == "master"
    start_branch(clone, "self-update/zzz")
    (clone / "x.md").write_text("x\n")
    assert commit_all(clone, "x") is True
    files, _ = diff_stats(clone)
    assert files == ["x.md"]
    discard_branch(clone, "self-update/zzz")
    head = _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert head == "master"
