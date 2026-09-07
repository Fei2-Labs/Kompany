"""Workspace-local artifact repo: ``<data_dir>/artifacts/{souls,workflows,plugins}``.

Git-backed so every evolved artifact is traceable and revertible. The
workspace is created lazily and never touches site-packages, the running
checkout or the self-update clone arena (same invariant family as
``core/harness/safety.py``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

WORKSPACE_DIRNAME = "artifacts"
SUBDIRS: tuple[str, ...] = ("souls", "workflows", "plugins")
_GIT_IDENTITY = ["-c", "user.name=kompany", "-c", "user.email=kompany@localhost", "-c", "commit.gpgsign=false"]


def workspace_root(data_dir: Path | str | None = None) -> Path:
    """``<data_dir>/artifacts``; resolves the data dir like the engine does."""
    if data_dir is None:
        env = os.environ.get("KOMPANY_DATA_DIR", "").strip()
        if env:
            data_dir = Path(env).expanduser()
        else:
            try:
                from kompany.config.settings import KompanySettings

                data_dir = KompanySettings.load().data_dir
            except Exception:  # noqa: BLE001 — loader must work in any bootstrap state
                data_dir = Path("~/.kompany").expanduser()
    return Path(data_dir) / WORKSPACE_DIRNAME


class ArtifactWorkspace:
    def __init__(self, data_dir: Path | str):
        self.root = workspace_root(data_dir)

    # -- layout -------------------------------------------------------------

    @property
    def souls(self) -> Path:
        return self.root / "souls"

    @property
    def workflows(self) -> Path:
        return self.root / "workflows"

    @property
    def plugins(self) -> Path:
        return self.root / "plugins"

    def exists(self) -> bool:
        return (self.root / ".git").exists()

    def ensure(self) -> Path:
        """Create dirs + ``git init`` + an initial commit; idempotent."""
        for sub in SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
            keep = self.root / sub / ".gitkeep"
            if not keep.exists():
                keep.write_text("")
        if not (self.root / ".git").exists():
            self._git("init", "-q")
            self._git("add", "-A")
            self._git("commit", "-q", "-m", "Initialize Kompany artifact workspace", identity=True)
        return self.root

    # -- git ------------------------------------------------------------------

    def _git(self, *args: str, identity: bool = False) -> subprocess.CompletedProcess[str]:
        cmd = ["git", "-C", str(self.root)]
        if identity:
            cmd += _GIT_IDENTITY
        return subprocess.run(cmd + list(args), capture_output=True, text=True)

    def head(self) -> str | None:
        p = self._git("rev-parse", "HEAD")
        return p.stdout.strip() or None if p.returncode == 0 else None

    def is_dirty(self) -> bool:
        p = self._git("status", "--porcelain")
        return bool(p.stdout.strip())

    def commit(self, message: str) -> str | None:
        """Stage everything and commit; returns the new sha or None when clean."""
        self.ensure()
        self._git("add", "-A")
        if not self.is_dirty() and not self._git("diff", "--cached", "--quiet").returncode:
            return None
        p = self._git("commit", "-q", "-m", message, identity=True)
        if p.returncode != 0:
            raise RuntimeError(f"artifact workspace commit failed: {(p.stderr or '').strip()[-300:]}")
        return self.head()

    def revert(self, sha: str, reason: str = "") -> str | None:
        """``git revert`` one commit (no in-place mutation); returns the revert sha."""
        p = self._git("revert", "--no-edit", sha, identity=True)
        if p.returncode != 0:
            self._git("revert", "--abort")
            raise RuntimeError(f"artifact workspace revert failed: {(p.stderr or '').strip()[-300:]}")
        if reason:
            self._git("commit", "--amend", "-q", "-m", f"Revert {sha[:7]}: {reason}", identity=True)
        return self.head()

    def log(self, limit: int = 20) -> list[dict[str, str]]:
        p = self._git("log", f"-{int(limit)}", "--pretty=%H%x1f%aI%x1f%s")
        rows: list[dict[str, str]] = []
        if p.returncode != 0:
            return rows
        for line in p.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                rows.append({"sha": parts[0], "at": parts[1], "message": parts[2]})
        return rows

    # -- contents ---------------------------------------------------------------

    def soul_paths(self) -> list[Path]:
        return sorted(p for p in self.souls.glob("*.yaml") if p.is_file()) if self.souls.is_dir() else []

    def workflow_paths(self) -> list[Path]:
        return sorted(p for p in self.workflows.glob("*.yaml") if p.is_file()) if self.workflows.is_dir() else []

    def plugin_dirs(self) -> list[Path]:
        return sorted(p for p in self.plugins.iterdir() if p.is_dir()) if self.plugins.is_dir() else []

    def status(self) -> dict:
        return {
            "root": str(self.root), "initialized": self.exists(), "head": self.head() if self.exists() else None,
            "dirty": self.is_dirty() if self.exists() else False,
            "souls": [p.name for p in self.soul_paths()], "workflows": [p.name for p in self.workflow_paths()],
            "plugins": [p.name for p in self.plugin_dirs()],
        }


__all__ = ["ArtifactWorkspace", "SUBDIRS", "WORKSPACE_DIRNAME", "workspace_root"]
