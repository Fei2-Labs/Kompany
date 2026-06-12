"""Dedicated clone arena for self-update sessions (PRD D1).

The clone lives at ``<data_dir>/self_update/repo`` — never the running
checkout. ``assert_workspace_safe`` is path-based against the RUNNING
source root, so the clone passes by construction while the running tree
keeps refusing harness sessions (constitution split, zero guard changes).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kompany.core.harness.safety import kompany_source_root

_GIT_IDENTITY = [
    "-c",
    "user.name=kompany",
    "-c",
    "user.email=kompany@localhost",
    "-c",
    "commit.gpgsign=false",
]


def _git(
    repo: Path, *args: str, identity: bool = False
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-C", str(repo)]
    if identity:
        cmd += _GIT_IDENTITY
    cmd += list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def _stderr_tail(proc: subprocess.CompletedProcess[str], limit: int = 300) -> str:
    return (proc.stderr or "").strip()[-limit:] or "(no stderr)"


def default_branch(clone: Path) -> str:
    """``main`` when it exists, else ``master``."""
    proc = _git(clone, "rev-parse", "--verify", "--quiet", "refs/heads/main")
    return "main" if proc.returncode == 0 else "master"


def ensure_clone(data_dir: Path | str, source_hint: Path | None = None) -> Path:
    """Materialize (or refresh) the clone at ``<data_dir>/self_update/repo``.

    Existing clone → best-effort ``git fetch origin`` (offline ok) and
    return. Otherwise clone from the source repo's ``origin`` remote URL;
    when that fails (offline / no remote), fall back to a local-path
    clone of the source checkout itself. Raises :class:`RuntimeError`
    only when every clone attempt fails.
    """
    target = Path(data_dir) / "self_update" / "repo"
    if (target / ".git").exists():
        _git(target, "fetch", "origin")  # best-effort; offline is fine
        return target

    source = Path(source_hint) if source_hint is not None else kompany_source_root()
    target.parent.mkdir(parents=True, exist_ok=True)

    attempts: list[str] = []
    origin_proc = _git(source, "remote", "get-url", "origin")
    if origin_proc.returncode == 0 and origin_proc.stdout.strip():
        origin_url = origin_proc.stdout.strip()
        proc = subprocess.run(
            ["git", "clone", origin_url, str(target)],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return target
        attempts.append(f"origin {origin_url}: {_stderr_tail(proc)}")

    proc = subprocess.run(
        ["git", "clone", str(source), str(target)],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return target
    attempts.append(f"local {source}: {_stderr_tail(proc)}")
    raise RuntimeError(
        "self-update clone failed — " + "; ".join(attempts)
    )


def start_branch(clone: Path, name: str) -> None:
    """Check out a fresh branch ``name`` off the up-to-date default branch."""
    base = default_branch(clone)
    proc = _git(clone, "checkout", base)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git checkout {base} failed in {clone}: {_stderr_tail(proc)}"
        )
    _git(clone, "pull", "--ff-only")  # best-effort; offline / no upstream ok
    proc = _git(clone, "checkout", "-b", name)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git checkout -b {name} failed in {clone}: {_stderr_tail(proc)}"
        )


def diff_stats(clone: Path) -> tuple[list[str], str]:
    """Changed files (committed or not, vs the branch base) + diff stat.

    Files = ``git diff --name-only <base>...HEAD`` union the working
    tree's ``git status --porcelain`` paths; the summary string is
    ``git diff --stat <base>`` (working tree + commits vs base).
    """
    base = default_branch(clone)
    files: set[str] = set()
    diff = _git(clone, "diff", "--name-only", f"{base}...HEAD")
    if diff.returncode == 0:
        files.update(ln.strip() for ln in diff.stdout.splitlines() if ln.strip())
    status = _git(clone, "status", "--porcelain")
    if status.returncode == 0:
        for line in status.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:  # rename: keep the destination
                path = path.split(" -> ", 1)[1].strip()
            if path:
                files.add(path.strip('"'))
    stat = _git(clone, "diff", "--stat", base)
    summary = stat.stdout.strip() if stat.returncode == 0 else ""
    return sorted(files), summary


def commit_all(clone: Path, message: str) -> bool:
    """``git add -A`` + commit with the kompany identity.

    Returns ``False`` when there is nothing to commit.
    """
    _git(clone, "add", "-A")
    status = _git(clone, "status", "--porcelain")
    if status.returncode == 0 and not status.stdout.strip():
        return False
    proc = _git(clone, "commit", "--quiet", "-m", message, identity=True)
    return proc.returncode == 0


def discard_branch(clone: Path, name: str) -> None:
    """Drop branch ``name`` (T3 abort path). Best-effort, never raises."""
    _git(clone, "checkout", default_branch(clone))
    _git(clone, "branch", "-D", name)


def push_branch(clone: Path, name: str) -> tuple[bool, str]:
    """Push the proposal branch to origin (post-approve effect, PR2).

    NEVER pushes the default branch — the merge gate stays human. Returns
    ``(ok, detail)``; auth/network failures surface in ``detail`` instead
    of raising (the effect records them on the proposal).
    """
    if name in ("main", "master"):
        return False, "refusing to push a default branch"
    proc = _git(clone, "push", "origin", name)
    if proc.returncode == 0:
        return True, f"pushed origin/{name}"
    return False, _stderr_tail(proc)


def create_pr(clone: Path, name: str, title: str, body: str) -> tuple[str | None, str]:
    """Best-effort ``gh pr create`` for the pushed branch.

    Returns ``(pr_url, detail)``. ``gh`` missing or unauthenticated is an
    expected condition — recorded, never raised. ``--head`` pins the
    branch so gh never guesses.
    """
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--head",
                name,
            ],
            cwd=str(clone),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return None, "gh CLI not installed — branch pushed, open the PR manually"
    except subprocess.TimeoutExpired:
        return None, "gh pr create timed out — branch pushed, open the PR manually"
    if proc.returncode == 0:
        url = (proc.stdout or "").strip().splitlines()[-1:]
        return (url[0] if url else None), "pr created"
    return None, f"gh pr create failed: {_stderr_tail(proc)}"


__all__ = [
    "commit_all",
    "create_pr",
    "default_branch",
    "diff_stats",
    "discard_branch",
    "ensure_clone",
    "push_branch",
    "start_branch",
]
