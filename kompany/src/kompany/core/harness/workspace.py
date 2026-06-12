"""Per-project git workspaces — the shared substrate across vehicles (PRD D4).

``ensure_workspace`` is idempotent and offline: mkdir + ``git init`` (codex
requires a repo; diff evidence needs one) + a ``KOMPANY.md`` context stub
written only when missing so vehicle sessions never clobber founder edits.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_KOMPANY_MD_STUB = """\
# Kompany Workspace

This directory is the working tree for a Kompany project. Agent sessions run
here and may create, edit, and commit files freely within this workspace. The
Kompany engine's own state (tasks, budgets, checkpoints in its database) is
the authoritative record of progress — this workspace is the shared substrate
that any execution vehicle continues from, not the source of truth.
"""


def git_files_changed(workspace: Path) -> list[str]:
    """Modified + untracked files in ``workspace``; ``[]`` if not a git repo.

    Diff evidence for outcome classification (PRD D6), shared by every
    vehicle adapter. In a fresh repo with no commits ``git diff HEAD``
    fails (no HEAD ref yet) and is skipped — every file is untracked
    then, so the ``ls-files --others`` scan still captures the full
    evidence set.
    """
    ws = Path(workspace)
    if not (ws / ".git").exists():
        return []
    files: set[str] = set()
    diff = subprocess.run(
        ["git", "-C", str(ws), "diff", "--name-only", "HEAD"],
        capture_output=True,
        text=True,
    )
    if diff.returncode == 0:
        files.update(ln.strip() for ln in diff.stdout.splitlines() if ln.strip())
    untracked = subprocess.run(
        ["git", "-C", str(ws), "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
    )
    if untracked.returncode == 0:
        files.update(
            ln.strip() for ln in untracked.stdout.splitlines() if ln.strip()
        )
    return sorted(files)


def ensure_workspace(data_dir: Path | str, project_id: str) -> Path:
    """Create (or reuse) ``<data_dir>/workspaces/<project_id>``.

    Initializes a git repo if one is not already present and writes the
    ``KOMPANY.md`` context stub only if missing. No network access.
    """
    if (
        not project_id
        or project_id in {".", ".."}
        or "/" in project_id
        or "\\" in project_id
    ):
        raise ValueError(f"invalid project_id for workspace: {project_id!r}")
    workspace = Path(data_dir) / "workspaces" / project_id
    workspace.mkdir(parents=True, exist_ok=True)
    if not (workspace / ".git").exists():
        proc = subprocess.run(
            ["git", "init", "--quiet"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"git init failed in {workspace}: "
                f"{(proc.stderr or '').strip()[:300] or '(no stderr)'}"
            )
    kompany_md = workspace / "KOMPANY.md"
    if not kompany_md.exists():
        kompany_md.write_text(_KOMPANY_MD_STUB, encoding="utf-8")
        # Commit the stub immediately: an uncommitted KOMPANY.md shows up in
        # git_files_changed forever after, handing every run (including ones
        # that did no work) false diff evidence for the D6 outcome check.
        subprocess.run(
            ["git", "-C", str(workspace), "add", "KOMPANY.md"],
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "-c",
                "user.name=kompany",
                "-c",
                "user.email=kompany@localhost",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--quiet",
                "-m",
                "Initialize Kompany workspace",
            ],
            capture_output=True,
            text=True,
        )
    return workspace
