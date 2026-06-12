"""Tests for per-project workspace creation (real local git, no network)."""

from __future__ import annotations

import subprocess

import pytest

from kompany.core.harness import ensure_workspace, git_files_changed


def test_creates_workspace_with_git_and_stub(tmp_path):
    workspace = ensure_workspace(tmp_path, "proj-1")
    assert workspace == tmp_path / "workspaces" / "proj-1"
    assert workspace.is_dir()
    assert (workspace / ".git").exists()
    stub = workspace / "KOMPANY.md"
    assert stub.exists()
    content = stub.read_text(encoding="utf-8")
    assert "Kompany" in content
    assert "authoritative" in content


def test_git_init_idempotent(tmp_path):
    first = ensure_workspace(tmp_path, "proj-1")
    second = ensure_workspace(tmp_path, "proj-1")
    assert first == second
    assert (second / ".git").exists()


def test_existing_kompany_md_not_overwritten(tmp_path):
    workspace = ensure_workspace(tmp_path, "proj-1")
    (workspace / "KOMPANY.md").write_text("founder edits", encoding="utf-8")
    ensure_workspace(tmp_path, "proj-1")
    assert (workspace / "KOMPANY.md").read_text(encoding="utf-8") == "founder edits"


def test_separate_projects_get_separate_workspaces(tmp_path):
    a = ensure_workspace(tmp_path, "proj-a")
    b = ensure_workspace(tmp_path, "proj-b")
    assert a != b
    assert a.parent == b.parent == tmp_path / "workspaces"


def test_accepts_str_data_dir(tmp_path):
    workspace = ensure_workspace(str(tmp_path), "proj-1")
    assert workspace.is_dir()


@pytest.mark.parametrize(
    "bad_id", ["", ".", "..", "a/b", "a\\b", "../escape", "/abs"]
)
def test_invalid_project_id_rejected(tmp_path, bad_id):
    with pytest.raises(ValueError, match="project_id"):
        ensure_workspace(tmp_path, bad_id)


# ---------------------------------------------------------------------------
# Diff evidence (PRD D6) — git_files_changed
# ---------------------------------------------------------------------------


def _commit_all(workspace) -> None:
    subprocess.run(["git", "-C", str(workspace), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.email=test@kompany.local",
            "-c",
            "user.name=test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-qm",
            "init",
        ],
        check=True,
    )


def test_files_changed_non_git_dir_returns_empty(tmp_path):
    assert git_files_changed(tmp_path) == []


def test_files_changed_fresh_repo_no_commits(tmp_path):
    # ensure_workspace commits the KOMPANY.md stub at init, so a fresh
    # workspace reports NO changes — an idle run must never inherit false
    # diff evidence from the stub (live-verified failure mode). Only real
    # work shows up.
    workspace = ensure_workspace(tmp_path, "proj-1")
    assert git_files_changed(workspace) == []
    (workspace / "new.txt").write_text("x", encoding="utf-8")
    assert git_files_changed(workspace) == ["new.txt"]


def test_files_changed_modified_and_untracked_after_commit(tmp_path):
    workspace = ensure_workspace(tmp_path, "proj-1")
    _commit_all(workspace)
    assert git_files_changed(workspace) == []
    # Edits to the committed stub DO count — that is real work on a real file.
    (workspace / "KOMPANY.md").write_text("edited", encoding="utf-8")
    (workspace / "b.txt").write_text("b", encoding="utf-8")
    assert git_files_changed(workspace) == ["KOMPANY.md", "b.txt"]
