"""Unit tests for ``POST /onboarding/ping`` — network error path.

Verifies that connection / DNS / timeout failures get bucketed into
``error_code='network'``.
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
    monkeypatch.delenv("KOMPANY_TEST_MODE", raising=False)


@pytest.fixture
def client() -> TestClient:
    api_module.reset_engine()
    return TestClient(api_module.app)


def test_ping_endpoint_connection_refused_returns_network(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connection-refused error → error_code='network'."""

    def fake_ping(provider: str, api_key: str, **kwargs: Any) -> tuple[bool, str]:
        return False, "APIConnectionError: Connection refused"

    monkeypatch.setattr(
        "kompany.installer.onboard._ping_llm", fake_ping
    )

    res = client.post(
        "/onboarding/ping",
        json={"provider": "anthropic", "api_key": "sk-ant-fake"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["model"] is None
    assert body["pricing"] is None
    assert body["error_code"] == "network"
    assert "Connection refused" in (body["error_message"] or "")


def test_ping_endpoint_dns_failure_returns_network(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DNS resolution failures land in network too."""

    def fake_ping(provider: str, api_key: str, **kwargs: Any) -> tuple[bool, str]:
        return False, "ConnectionError: Name or service not known"

    monkeypatch.setattr(
        "kompany.installer.onboard._ping_llm", fake_ping
    )

    res = client.post(
        "/onboarding/ping",
        json={"provider": "anthropic", "api_key": "sk-ant-fake"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["error_code"] == "network"


def test_ping_endpoint_timeout_returns_network(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read timeouts are a network-class failure for the founder."""

    def fake_ping(provider: str, api_key: str, **kwargs: Any) -> tuple[bool, str]:
        return False, "ReadTimeout: Request timed out after 60s"

    monkeypatch.setattr(
        "kompany.installer.onboard._ping_llm", fake_ping
    )

    res = client.post(
        "/onboarding/ping",
        json={"provider": "anthropic", "api_key": "sk-ant-fake"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["error_code"] == "network"
