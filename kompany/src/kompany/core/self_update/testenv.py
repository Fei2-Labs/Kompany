"""Test interpreter for self-update proposals.

The engine runs from a release venv that carries no test dependencies, so a
proposal's tests used to die at collection (``No module named 'fastapi'``)
and the founder got a card that said FAILED for reasons unrelated to the
change. Proposals now run their tests in a dedicated venv under
``<data_dir>/self_update/test-venv``, with the clone installed editable plus
the ``api``, ``mcp`` and ``dev`` extras. The venv is rebuilt only when the
clone's ``pyproject.toml`` changes.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

_EXTRAS = "api,mcp,dev"
_SETUP_TIMEOUT_SECONDS = 900

# Output markers of a test run that never reached the change under test.
ENV_ERROR_MARKERS: tuple[str, ...] = (
    "ModuleNotFoundError",
    "ImportError while importing",
    "errors during collection",
    "No module named pytest",
    "test environment unavailable",
)


class TestEnvError(RuntimeError):
    """The test venv could not be built."""


def is_env_error(summary: str) -> bool:
    """True when a test summary failed on setup, not on the change itself."""
    return any(m in (summary or "") for m in ENV_ERROR_MARKERS)


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _can_run_suite(python: str) -> bool:
    """The interpreter already has the test runner and the API extras."""
    try:
        probe = subprocess.run([python, "-c", "import pytest, fastapi"],
                               capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def ensure_test_python(data_dir: Path | str, clone: Path) -> Path:
    """Return a python that can run the clone's test suite; build it if needed.

    A dev checkout's interpreter usually has everything, so it is used as is.
    Only a release venv (no pytest, no API extras) gets the dedicated venv.
    """
    if _can_run_suite(sys.executable):
        return Path(sys.executable)
    pkg = clone / "kompany"
    pyproject = pkg / "pyproject.toml"
    if not pyproject.is_file():
        raise TestEnvError(f"no pyproject.toml at {pkg}")
    venv = Path(data_dir) / "self_update" / "test-venv"
    stamp = venv / ".kompany-pyproject.sha256"
    digest = hashlib.sha256(pyproject.read_bytes()).hexdigest()
    python = _venv_python(venv)
    if python.is_file() and stamp.is_file() and stamp.read_text().strip() == digest:
        return python
    try:
        if not python.is_file():
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True,
                           capture_output=True, text=True, timeout=_SETUP_TIMEOUT_SECONDS)
        subprocess.run([str(python), "-m", "pip", "install", "-q", "-e", f"{pkg}[{_EXTRAS}]"],
                       check=True, capture_output=True, text=True, timeout=_SETUP_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise TestEnvError(f"test environment unavailable: {str(detail).strip()[-300:]}") from exc
    stamp.write_text(digest)
    return python


__all__ = ["ENV_ERROR_MARKERS", "TestEnvError", "ensure_test_python", "is_env_error"]
