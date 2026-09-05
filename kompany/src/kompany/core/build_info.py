"""Build identity + staleness of the RUNNING engine vs the repo on disk (#26).

Founder confusion this closes: a fix is committed (or pulled) but the
server process still runs the code it loaded at start, so the fix is
"not working". We record the repo HEAD at import time — that is the code
this process is executing — and on every request compare it with the
current HEAD; when they differ, ``newer_commits`` counts how far the
running build is behind, so the UI can say "build abc123 · repo has 16
newer commits — restart to pick them up".

Cheap by design: HEAD is read from ``.git`` files (no subprocess) except
the one ``git rev-list --count`` when the two differ, which is cached per
head sha. Outside a git checkout (PyInstaller bundle, wheel) every git
field is ``"unknown"`` and ``stale`` is ``False``.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_STARTED_AT = datetime.now(UTC).isoformat()


def find_git_root(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def read_head_sha(git_root: Path) -> str | None:
    """Resolve HEAD → sha by reading .git files only (handles packed refs)."""
    git_dir = git_root / ".git"
    if git_dir.is_file():  # worktree: ".git" is a pointer file
        try:
            line = git_dir.read_text().strip()
            if line.startswith("gitdir:"):
                git_dir = (git_root / line.split(":", 1)[1].strip()).resolve()
        except OSError:
            return None
    try:
        head = (git_dir / "HEAD").read_text().strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head or None
    ref = head.split(":", 1)[1].strip()
    # worktrees keep HEAD locally but refs in the common dir
    for base in (git_dir, git_dir.parent.parent if git_dir.name.startswith("worktrees") is False else git_dir, _common_dir(git_dir)):
        if base is None:
            continue
        ref_file = base / ref
        if ref_file.exists():
            try:
                return ref_file.read_text().strip() or None
            except OSError:
                return None
        packed = base / "packed-refs"
        if packed.exists():
            try:
                for line in packed.read_text().splitlines():
                    if line.endswith(" " + ref):
                        return line.split(" ", 1)[0]
            except OSError:
                pass
    return None


def _common_dir(git_dir: Path) -> Path | None:
    cd = git_dir / "commondir"
    if cd.exists():
        try:
            return (git_dir / cd.read_text().strip()).resolve()
        except OSError:
            return None
    return None


_GIT_ROOT = find_git_root(Path(__file__).resolve())
_START_SHA = read_head_sha(_GIT_ROOT) if _GIT_ROOT else None
_NEWER_CACHE: dict[str, int] = {}


def _count_newer(git_root: Path, start_sha: str, head_sha: str) -> int | None:
    key = f"{start_sha}..{head_sha}"
    if key in _NEWER_CACHE:
        return _NEWER_CACHE[key]
    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", key], cwd=str(git_root),
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        n = int(out) if out.isdigit() else None
    except Exception:  # noqa: BLE001 — staleness is advisory
        n = None
    if n is not None:
        _NEWER_CACHE[key] = n
    return n


_UNSET: Any = object()


def staleness(git_root: Path | None | Any = _UNSET, start_sha: str | None | Any = _UNSET) -> dict[str, Any]:
    """Compare the sha this process started on with the repo's current HEAD.

    Defaults to this process's own repo/start sha; pass ``None`` explicitly
    to model a non-git install.
    """
    root = _GIT_ROOT if git_root is _UNSET else git_root
    start = _START_SHA if start_sha is _UNSET else start_sha
    if root is None or not start:
        return {"started_at": _STARTED_AT, "start_commit": "unknown", "repo_head": "unknown",
                "newer_commits": 0, "stale": False}
    head = read_head_sha(root) or "unknown"
    if head == "unknown" or head == start:
        return {"started_at": _STARTED_AT, "start_commit": start[:7], "repo_head": head[:7] if head != "unknown" else head,
                "newer_commits": 0, "stale": False}
    newer = _count_newer(root, start, head)
    return {
        "started_at": _STARTED_AT,
        "start_commit": start[:7],
        "repo_head": head[:7],
        "newer_commits": newer if newer is not None else 1,
        "stale": True,
        "hint": "The running engine predates the repo — restart (or rebuild the desktop bundle) to pick up the newer commits.",
    }


__all__ = ["find_git_root", "read_head_sha", "staleness"]
