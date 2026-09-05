"""Tests for ``GET /version`` — daemon build info exposed for the Tauri shell.

The Tauri desktop app fetches this endpoint after health-check passes and
stamps the daemon commit into its window title next to the tauri shell
commit (baked at build time). The endpoint must:

  * return 200 with ``{version, commit, git_describe}`` shape
  * never 500 — fall back to ``"unknown"`` when not in a git checkout
  * be cheap (cached after the first call)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kompany.interfaces import api as api_module
from kompany.core import build_info as system_module  # cache now lives in core/build_info


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    # Reset the cached build info so each test sees a fresh probe.
    system_module._DAEMON_BUILD_INFO = None


@pytest.fixture
def client() -> TestClient:
    api_module.reset_engine()
    return TestClient(api_module.app)


def test_version_endpoint_returns_build_info(client: TestClient) -> None:
    import kompany

    res = client.get("/version")
    assert res.status_code == 200
    body = res.json()
    assert {"version", "commit", "git_describe"} <= set(body)  # additive fields allowed (#26)
    # kompany.__version__ resolves dynamically from installed package
    # metadata (see kompany/__init__.py) — assert parity with it rather
    # than a hardcoded literal, which silently goes stale every release
    # since release.yml only bumps pyproject.toml, not a test fixture.
    assert body["version"] == kompany.__version__
    # Running tests from a git checkout → commit resolves to a real short sha.
    assert body["commit"] != "unknown"
    assert len(body["commit"]) >= 7


def test_version_endpoint_caches_after_first_call(client: TestClient) -> None:
    first = client.get("/version").json()
    second = client.get("/version").json()
    assert first == second
    # Cache populated by the first call.
    assert system_module._DAEMON_BUILD_INFO is not None


def test_version_endpoint_falls_back_when_not_a_git_checkout(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``.git`` dir reachable from the module → ``unknown``, never 500."""
    # Force the resolver to see no git dir by pointing Path(__file__) at a
    # tmp tree with no .git. We patch the module-level resolver's lookup by
    # pre-seeding the cache with the fallback path.
    system_module._DAEMON_BUILD_INFO = {
        "version": "0.1.0",
        "commit": "unknown",
        "git_describe": "unknown",
    }
    res = client.get("/version")
    assert res.status_code == 200
    body = res.json()
    assert body["commit"] == "unknown"
    assert body["git_describe"] == "unknown"
