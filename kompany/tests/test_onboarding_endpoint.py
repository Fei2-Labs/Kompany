"""Unit tests for ``/onboarding/status`` and ``/onboarding/complete``.

We swap the production ``onboard_headless`` import target on the api
module so the REST handler exercises validation + response shaping
without a real engine — the headless function itself is covered by
``test_onboard_headless.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kompany.installer import OnboardError, OnboardResult
from kompany.installer.onboard import PROVIDER_ENV_VARS
from kompany.interfaces import api as api_module


@pytest.fixture(autouse=True)
def _isolate_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for var in PROVIDER_ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Reset the cached engine before every request — otherwise a prior
    # test bleeds its engine into the next.
    api_module.reset_engine()
    return TestClient(api_module.app)


def _seed_install(data_dir: Path, template_id: str = "saas-startup") -> None:
    import sqlite3

    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(data_dir / "kompany.db"))
    conn.execute("CREATE TABLE company_config (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE credential_vault (name TEXT PRIMARY KEY, ciphertext TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO company_config (key, value) VALUES ('template_id', ?)",
        (template_id,),
    )
    conn.execute(
        "INSERT INTO credential_vault (name, ciphertext, updated_at) "
        "VALUES ('anthropic_api_key', 'ciphertext', '2026-05-19')"
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# /onboarding/status
# ---------------------------------------------------------------------------


def test_status_reports_false_for_fresh_install(client: TestClient) -> None:
    res = client.get("/onboarding/status")
    assert res.status_code == 200
    body = res.json()
    assert body == {"onboarded": False, "template_id": None, "provider": None}


def test_status_reports_true_after_install(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_install(Path(tmp_path / "data"), template_id="saas-startup")
    res = client.get("/onboarding/status")
    assert res.status_code == 200
    body = res.json()
    assert body["onboarded"] is True
    assert body["template_id"] == "saas-startup"
    assert body["provider"] == "anthropic"


# ---------------------------------------------------------------------------
# /onboarding/complete
# ---------------------------------------------------------------------------


def test_complete_with_valid_body_returns_ready(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_headless(
        *,
        data_dir: Any,
        provider: str,
        api_key: str,
        template_id: str,
        directive: str | None = None,
        base_url: str | None = None,
    ) -> OnboardResult:
        captured["call"] = {
            "provider": provider,
            "api_key": api_key,
            "template_id": template_id,
            "directive": directive,
            "base_url": base_url,
            "data_dir": str(data_dir),
        }
        return OnboardResult(
            status="completed",
            data_dir=Path(data_dir),
            provider=provider,
            template_id=template_id,
            api_key_storage="vault",
            ping_status="skipped_test_mode",
        )

    # Drop the import inside the handler so monkeypatching works against
    # the module attribute.
    import kompany.installer as installer

    monkeypatch.setattr(installer, "onboard_headless", fake_headless)

    res = client.post(
        "/onboarding/complete",
        json={
            "provider": "anthropic",
            "api_key": "sk-ant-fake",
            "template_id": "blank",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ready"
    assert body["template_id"] == "blank"
    assert body["provider"] == "anthropic"
    assert body["message"] is None
    assert body["code"] is None
    assert captured["call"]["provider"] == "anthropic"
    assert captured["call"]["template_id"] == "blank"


def test_complete_missing_fields_returns_422(client: TestClient) -> None:
    # Pydantic v2 with extra=forbid + min_length=1 rejects empty bodies.
    res = client.post("/onboarding/complete", json={})
    assert res.status_code == 422

    # Extra unknown field also rejected.
    res = client.post(
        "/onboarding/complete",
        json={
            "provider": "anthropic",
            "api_key": "x",
            "template_id": "blank",
            "rogue": "field",
        },
    )
    assert res.status_code == 422


def test_complete_with_ping_failure_returns_error_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_headless(**kwargs: Any) -> OnboardResult:
        raise OnboardError("ping_failed", "anthropic ping failed: 401 invalid key")

    import kompany.installer as installer

    monkeypatch.setattr(installer, "onboard_headless", fake_headless)

    res = client.post(
        "/onboarding/complete",
        json={
            "provider": "anthropic",
            "api_key": "sk-bad",
            "template_id": "blank",
        },
    )
    # Errors surface as 200 + status='error' so the JS form can render
    # them inline rather than parsing FastAPI's error envelope.
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"
    assert body["code"] == "ping_failed"
    assert "401 invalid key" in (body["message"] or "")


def test_complete_with_unknown_provider_surfaces_error_inline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Real (un-mocked) call path: provider validation lives inside
    # onboard_headless, so we let the real function run and trust the
    # OnboardError mapping in the handler.
    res = client.post(
        "/onboarding/complete",
        json={
            "provider": "bogus",
            "api_key": "x",
            "template_id": "blank",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"
    assert body["code"] == "unknown_provider"


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_returns_200_without_engine(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
