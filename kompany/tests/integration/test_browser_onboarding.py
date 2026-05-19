"""Integration: simulate the in-window onboarding flow end-to-end.

Drives the real FastAPI app against a real :class:`KompanyEngine` in a
tmp data dir, exactly the way the Tauri WebView's onboarding form
will:

  1. GET /onboarding/status → ``onboarded == False``
  2. POST /onboarding/complete → ``{status: 'ready'}``
  3. GET /onboarding/status → ``onboarded == True``
  4. GET /agents/status → 11 C-suite rows

LLM ping is bypassed via ``KOMPANY_TEST_MODE=1``. The vault key is
generated per-test so the real cryptography path runs without leaving
state behind.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from kompany.installer.onboard import PROVIDER_ENV_VARS
from kompany.interfaces import api as api_module


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    monkeypatch.setenv("KOMPANY_VAULT_KEY", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    for var in PROVIDER_ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    # Fresh engine instance for every test.
    api_module.reset_engine()


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_module.app)


def test_browser_onboarding_end_to_end(client: TestClient) -> None:
    # 1. Fresh install: status reports onboarded=False.
    res = client.get("/onboarding/status")
    assert res.status_code == 200
    snap = res.json()
    assert snap["onboarded"] is False
    assert snap["template_id"] is None

    # 2. Complete onboarding with a real engine + real template apply.
    res = client.post(
        "/onboarding/complete",
        json={
            "provider": "anthropic",
            "api_key": "sk-ant-test-key",
            "template_id": "blank",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ready", body
    assert body["template_id"] == "blank"
    assert body["provider"] == "anthropic"

    # 3. Re-poll status: onboarded flips to True with the right template.
    res = client.get("/onboarding/status")
    assert res.status_code == 200
    snap = res.json()
    assert snap["onboarded"] is True
    assert snap["template_id"] == "blank"
    assert snap["provider"] == "anthropic"

    # 4. The main dashboard precondition: /agents/status returns the
    # 11 canonical C-suite rows in display order.
    res = client.get("/agents/status")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 11
    roles = [r["role"] for r in rows]
    # Must match api.C_SUITE_ROLES, uppercased.
    assert roles == [
        "CEO", "CFO", "CTO", "CPO", "CMO", "CRO",
        "COO", "CSA", "CISO", "COS", "CV",
    ]


def test_complete_is_idempotent_under_double_submit(client: TestClient) -> None:
    """A double-click on the submit button must not corrupt state."""

    payload = {
        "provider": "anthropic",
        "api_key": "sk-ant-test-key",
        "template_id": "saas-startup",
    }
    first = client.post("/onboarding/complete", json=payload)
    assert first.status_code == 200
    assert first.json()["status"] == "ready"

    # Second submission: must short-circuit through the reuse path.
    second = client.post("/onboarding/complete", json=payload)
    assert second.status_code == 200
    body = second.json()
    assert body["status"] == "ready"
    # Template should still be the originally-applied one.
    assert body["template_id"] == "saas-startup"
