"""Unit tests for ``POST /onboarding/ping`` — happy-path shape.

The handler reuses ``installer.onboard._ping_llm`` for the actual LLM
call; we monkeypatch it on the api module so the test exercises the
endpoint's response-shaping logic without touching the network.
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
    # Ensure tests can't accidentally exercise the real network path.
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")


@pytest.fixture
def client() -> TestClient:
    api_module.reset_engine()
    return TestClient(api_module.app)


def test_ping_endpoint_returns_ok_with_model_and_pricing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: ok=True, model populated, pricing populated, no error."""
    # ``KOMPANY_TEST_MODE=1`` already short-circuits ``_ping_llm`` to
    # return ``(True, "skipped_test_mode")``, but we also explicitly
    # patch to make the test resilient against environment leaks.
    captured: dict[str, Any] = {}

    def fake_ping(provider: str, api_key: str, **kwargs: Any) -> tuple[bool, str]:
        captured["provider"] = provider
        captured["api_key"] = api_key
        return True, "ok"

    monkeypatch.setattr(
        "kompany.installer.onboard._ping_llm", fake_ping
    )

    res = client.post(
        "/onboarding/ping",
        json={"provider": "anthropic", "api_key": "sk-ant-fake"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["error_code"] is None
    assert body["error_message"] is None
    # The default Anthropic ping model is the economy tier, which is in
    # the PRICING table so we expect a non-null pricing block.
    assert body["model"] is not None
    assert isinstance(body["pricing"], dict)
    assert "in_per_mtok" in body["pricing"]
    assert "out_per_mtok" in body["pricing"]
    assert isinstance(body["pricing"]["in_per_mtok"], (int, float))
    assert isinstance(body["pricing"]["out_per_mtok"], (int, float))
    # Confirm the handler forwarded the provider + key to _ping_llm.
    assert captured["provider"] == "anthropic"
    assert captured["api_key"] == "sk-ant-fake"


def test_ping_endpoint_rejects_extra_fields(client: TestClient) -> None:
    """``extra='forbid'`` means rogue keys are 422'd instead of ignored."""
    res = client.post(
        "/onboarding/ping",
        json={
            "provider": "anthropic",
            "api_key": "sk-ant-fake",
            "rogue": "field",
        },
    )
    assert res.status_code == 422


def test_ping_endpoint_requires_provider_and_key(client: TestClient) -> None:
    """Missing fields → 422 from Pydantic min_length=1 + required."""
    res = client.post("/onboarding/ping", json={})
    assert res.status_code == 422

    res = client.post("/onboarding/ping", json={"provider": "anthropic"})
    assert res.status_code == 422
