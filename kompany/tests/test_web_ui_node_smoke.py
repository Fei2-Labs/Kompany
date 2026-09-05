"""Run the vanilla web_ui node smoke tests (``tests/*_smoke.mjs``) under pytest.

The dashboard is no-build ES modules, so its logic tests are plain node
scripts. This wrapper makes them part of ``python -m pytest`` and CI; it
skips cleanly when node is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TESTS = Path(__file__).parent
_SCRIPTS = sorted(_TESTS.glob("*_smoke.mjs"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("script", _SCRIPTS, ids=[s.name for s in _SCRIPTS])
def test_web_ui_smoke(script: Path) -> None:
    proc = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=60, cwd=str(_TESTS)
    )
    assert proc.returncode == 0, f"{script.name} failed:\n{proc.stdout}\n{proc.stderr}"
    assert "ok" in proc.stdout.lower()
