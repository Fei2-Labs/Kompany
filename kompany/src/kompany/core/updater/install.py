"""Release layout + venv install + atomic switch under ``<data_dir>/releases``.

    <data_dir>/releases/<version>/venv/     one venv per release
    <data_dir>/releases/current -> <version> what the supervisor runs

The daemon's ExecStart points at ``current/venv/bin/python``, so flipping
the symlink and exiting is the whole deploy; the previous release stays on
disk for rollback.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


def releases_dir(data_dir: Path | str) -> Path:
    return Path(data_dir) / "releases"


def current_link(data_dir: Path | str) -> Path:
    return releases_dir(data_dir) / "current"


def venv_python(release_dir: Path) -> Path:
    return release_dir / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def layout(data_dir: Path | str, executable: str | None = None) -> dict[str, Any]:
    """Where this process runs from and what ``current`` points to.

    Detection uses the interpreter *prefix* (the venv root), not
    ``sys.executable``: a venv's ``bin/python`` is a symlink to the system
    interpreter, so resolving it would always escape the releases dir.
    """
    rd = releases_dir(data_dir)
    here = Path(executable) if executable else Path(sys.prefix)
    candidates = {here, here.resolve()}
    try:
        rd_resolved = rd.resolve()
    except OSError:
        rd_resolved = rd
    running_from_release = rd.exists() and any(
        rd in c.parents or rd_resolved in c.parents for c in candidates
    )
    current = None
    try:
        if current_link(data_dir).is_symlink():
            current = current_link(data_dir).resolve().name
    except OSError:
        current = None
    return {
        "releases_dir": str(rd), "current": current, "running_from_release": running_from_release,
        "frozen": bool(getattr(sys, "frozen", False)),
        "installed": sorted(p.name for p in rd.iterdir() if p.is_dir() and p.name != "current") if rd.is_dir() else [],
    }


def create_venv(release_dir: Path, *, run: Callable[..., Any] | None = None, python: str | None = None) -> Path:
    runner = run or subprocess.run
    release_dir.mkdir(parents=True, exist_ok=True)
    venv = release_dir / "venv"
    proc = runner([python or sys.executable, "-m", "venv", "--clear", str(venv)], capture_output=True, text=True, timeout=300)
    if getattr(proc, "returncode", 1) != 0:
        raise RuntimeError(f"venv creation failed: {(getattr(proc, 'stderr', '') or '').strip()[-400:]}")
    return venv


def pip_install(release_dir: Path, wheels: list[Path], *, extras: str = "api,mcp",
                run: Callable[..., Any] | None = None) -> str:
    """Install the wheels (Core with extras) into the release venv. Returns the tail of pip's output."""
    runner = run or subprocess.run
    py = venv_python(release_dir)
    specs: list[str] = []
    for w in wheels:
        specs.append(f"{w}[{extras}]" if w.name.startswith("kompany-") and extras else str(w))
    proc = runner([str(py), "-m", "pip", "install", "--upgrade", "--no-input", *specs],
                  capture_output=True, text=True, timeout=1800)
    out = ((getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")).strip()
    if getattr(proc, "returncode", 1) != 0:
        raise RuntimeError(f"pip install failed: {out[-600:]}")
    return out[-600:]


def installed_version(release_dir: Path, package: str = "kompany", *, run: Callable[..., Any] | None = None) -> str | None:
    runner = run or subprocess.run
    py = venv_python(release_dir)
    code = f"from importlib.metadata import version; print(version({package!r}))"
    try:
        proc = runner([str(py), "-c", code], capture_output=True, text=True, timeout=60)
    except Exception:  # noqa: BLE001
        return None
    return (getattr(proc, "stdout", "") or "").strip() or None if getattr(proc, "returncode", 1) == 0 else None


def site_packages(release_dir: Path) -> Path | None:
    """The venv's site-packages (``lib/pythonX.Y/site-packages``), if present."""
    hits = sorted((release_dir / "venv" / "lib").glob("python*/site-packages")) if (release_dir / "venv" / "lib").is_dir() else []
    return hits[0] if hits else None


def carry_over_package(src_release: Path, dst_release: Path, dist_name: str = "kompany_pro") -> str | None:
    """Copy a pure-Python distribution (package dir + dist-info) from the
    current release venv into the new one — used when the Pro release feed
    is unreachable (private repo, no token) so a Core update never strands
    the installed Pro. Same interpreter series required. Returns the carried
    version, or None when the source has no such distribution."""
    import shutil

    src_sp = site_packages(src_release)
    if src_sp is None:
        return None
    infos = sorted(src_sp.glob(f"{dist_name}-*.dist-info"))
    if not infos:
        return None
    info = infos[-1]
    version = info.name[len(dist_name) + 1:-len(".dist-info")]
    dst_sp = dst_release / "venv" / "lib" / src_sp.parent.name / "site-packages"
    dst_sp.mkdir(parents=True, exist_ok=True)
    tops = [t for t in (info / "top_level.txt").read_text().split() if t] if (info / "top_level.txt").is_file() else [dist_name]
    for top in tops:
        src_pkg = src_sp / top
        if src_pkg.is_dir():
            if (dst_sp / top).exists():
                shutil.rmtree(dst_sp / top)
            shutil.copytree(src_pkg, dst_sp / top, ignore=shutil.ignore_patterns("__pycache__"))
    if (dst_sp / info.name).exists():
        shutil.rmtree(dst_sp / info.name)
    shutil.copytree(info, dst_sp / info.name)
    return version


def switch_current(data_dir: Path | str, version: str) -> str | None:
    """Atomically point ``current`` at ``version``; returns the previous target name."""
    rd = releases_dir(data_dir)
    target = rd / version
    if not venv_python(target).exists():
        raise RuntimeError(f"release {version} has no venv at {target}")
    link = current_link(data_dir)
    previous = link.resolve().name if link.is_symlink() else None
    tmp = rd / f".current.{os.getpid()}"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(version, tmp)
    os.replace(tmp, link)
    return previous


def supervised() -> str | None:
    """'systemd' / 'launchd' / None — whether exiting will get us restarted."""
    if os.environ.get("KOMPANY_SUPERVISED"):
        return os.environ["KOMPANY_SUPERVISED"]
    if os.environ.get("INVOCATION_ID"):
        return "systemd"
    if os.environ.get("XPC_SERVICE_NAME", "").startswith("com.kompany"):
        return "launchd"
    return None


__all__ = ["carry_over_package", "create_venv", "current_link", "installed_version", "layout", "pip_install", "releases_dir",
           "site_packages", "supervised", "switch_current", "venv_python"]
