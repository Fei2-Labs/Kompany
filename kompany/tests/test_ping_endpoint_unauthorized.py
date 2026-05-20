"""Unit tests for ``POST /onboarding/ping`` — unauthorized error path.

Verifies that a 401-shaped failure from the LLM provider gets bucketed
into ``error_code='unauthorized'`` by the handler's classification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kompany.installer.onboard import PROVIDER_ENV_VARS
from kompany.interfaces import api as api_module


@pytest.fixture(autouse=True)
def _isolate_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for var in PROVIDER_ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    # We explicitly do NOT set KOMPANY_TEST_MODE here — the patch below
    # replaces _ping_llm directly so the bypass is unnecessary.
    monkeypatch.delenv("KOMPANY_TEST_MODE", raising=False)


@pytest.fixture
def client() -> TestClient:
    api_module.reset_engine()
    return TestClient(api_module.app)


def test_ping_endpoint_401_returns_unauthorized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """401 from provider → error_code='unauthorized', ok=False."""

    def fake_ping(provider: str, api_key: str, **kwargs: Any) -> tuple[bool, str]:
        # _ping_llm formats the failure as f"{type(exc).__name__}: {exc}".
        # An anthropic SDK 401 surfaces as ``AuthenticationError`` with a
        # message containing "401" and "invalid x-api-key".
        return False, "AuthenticationError: 401 unauthorized — invalid x-api-key"

    monkeypatch.setattr(
        "kompany.installer.onboard._ping_llm", fake_ping
    )

    res = client.post(
        "/onboarding/ping",
        json={"provider": "anthropic", "api_key": "sk-bad"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["model"] is None
    assert body["pricing"] is None
    assert body["error_code"] == "unauthorized"
    assert "401" in (body["error_message"] or "")


def test_ping_endpoint_forbidden_also_classified_unauthorized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """403 / PermissionDeniedError also lands in the unauthorized bucket."""

    def fake_ping(provider: str, api_key: str, **kwargs: Any) -> tuple[bool, str]:
        return False, "PermissionDeniedError: 403 forbidden"

    monkeypatch.setattr(
        "kompany.installer.onboard._ping_llm", fake_ping
    )

    res = client.post(
        "/onboarding/ping",
        json={"provider": "openai", "api_key": "sk-bad"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["error_code"] == "unauthorized"
