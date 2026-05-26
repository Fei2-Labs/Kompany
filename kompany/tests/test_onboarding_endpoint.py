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
    assert body == {
        "onboarded": False,
        "template_id": None,
        "provider": None,
        # Resume-from-* fields default to "no resume needed" on a
        # fresh install.
        "pending_target_feasibility_approval_id": None,
        "agreed_targets_set": False,
        "pending_first_move": False,
    }


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
        # Mission-targets task (05-19) — four optional knobs added to the
        # headless contract.
        initial_budget: float | None = None,
        revenue_target: float | None = None,
        customer_target: int | None = None,
        deadline: str | None = None,
        # Onboard-v2 task (05-19) — founder-edited glossary terms.
        glossary_overrides: dict[str, str] | None = None,
    ) -> OnboardResult:
        captured["call"] = {
            "provider": provider,
            "api_key": api_key,
            "template_id": template_id,
            "directive": directive,
            "base_url": base_url,
            "data_dir": str(data_dir),
            "initial_budget": initial_budget,
            "revenue_target": revenue_target,
            "customer_target": customer_target,
            "deadline": deadline,
            "glossary_overrides": glossary_overrides,
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


def test_env_defaults_empty_when_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh process with no CUSTOM_LLM_* env vars returns empty
    strings — wizard falls back to its blank state."""
    monkeypatch.delenv("CUSTOM_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("CUSTOM_LLM_API_KEY", raising=False)
    monkeypatch.delenv("KOMPANY_MODEL_PRIMARY", raising=False)
    monkeypatch.delenv("KOMPANY_MODEL_APEX", raising=False)
    res = client.get("/onboarding/env_defaults")
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "custom_base_url": "",
        "custom_api_key": "",
        "suggested_provider": "",
        "suggested_model": "",
    }


def test_env_defaults_returns_custom_when_both_set(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both CUSTOM_LLM_BASE_URL + CUSTOM_LLM_API_KEY set →
    suggested_provider='custom' so the wizard auto-picks the right
    slot. Model hint flows through from KOMPANY_MODEL_PRIMARY."""
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "https://swedeapi.example/v1/")
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "sk-test-1234")
    monkeypatch.setenv("KOMPANY_MODEL_PRIMARY", "gpt-5.5")
    res = client.get("/onboarding/env_defaults")
    assert res.status_code == 200
    body = res.json()
    assert body["custom_base_url"] == "https://swedeapi.example/v1/"
    assert body["custom_api_key"] == "sk-test-1234"
    assert body["suggested_provider"] == "custom"
    assert body["suggested_model"] == "gpt-5.5"


def test_env_defaults_partial_does_not_suggest_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only base_url set (no key) → suggested_provider stays empty so
    the wizard doesn't auto-pick custom with a half-configured pair."""
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "https://example/v1/")
    monkeypatch.delenv("CUSTOM_LLM_API_KEY", raising=False)
    res = client.get("/onboarding/env_defaults")
    body = res.json()
    assert body["custom_base_url"] == "https://example/v1/"
    assert body["suggested_provider"] == ""


def test_env_defaults_reads_data_dir_dotenv(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A GUI app launched from Finder doesn't inherit the shell env, so
    env_defaults must also read <data_dir>/.env. Process env stays
    clean here; the values come purely from the file."""
    monkeypatch.delenv("CUSTOM_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("CUSTOM_LLM_API_KEY", raising=False)
    data_dir = Path(tmp_path / "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ".env").write_text(
        'CUSTOM_LLM_BASE_URL="https://swedeapi.example/v1/"\n'
        "CUSTOM_LLM_API_KEY=sk-from-file\n"
        "KOMPANY_MODEL_PRIMARY=gpt-5.5\n",
        encoding="utf-8",
    )
    res = client.get("/onboarding/env_defaults")
    body = res.json()
    assert body["custom_base_url"] == "https://swedeapi.example/v1/"
    assert body["custom_api_key"] == "sk-from-file"
    assert body["suggested_provider"] == "custom"
    assert body["suggested_model"] == "gpt-5.5"


def test_stash_then_restore_credentials_roundtrip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mid-onboarding stash writes the encrypted vault; the restore
    endpoint reads it back so a wizard relaunch repopulates the key.
    A vault key must exist for the write to land."""
    monkeypatch.setenv("KOMPANY_VAULT_KEY", "C8WJOwHdhwcWnW2siGKVyEggFwVHe41ERKC1SFRgfJ8=")
    api_module.reset_engine()

    # Nothing stashed yet.
    res = client.get("/onboarding/stashed_credentials")
    assert res.status_code == 200
    assert res.json()["has_key"] is False

    # Stash a custom-provider credential.
    res = client.post(
        "/onboarding/stash_credentials",
        json={
            "provider": "custom",
            "api_key": "sk-stash-test",
            "base_url": "https://swedeapi.example/v1/",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["stored"] is True
    assert body["storage"] == "vault"

    # Restore reads it back decrypted.
    res = client.get("/onboarding/stashed_credentials")
    body = res.json()
    assert body["provider"] == "custom"
    assert body["api_key"] == "sk-stash-test"
    assert body["base_url"] == "https://swedeapi.example/v1/"
    assert body["has_key"] is True


def test_stash_rejects_unknown_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KOMPANY_VAULT_KEY", "C8WJOwHdhwcWnW2siGKVyEggFwVHe41ERKC1SFRgfJ8=")
    api_module.reset_engine()
    res = client.post(
        "/onboarding/stash_credentials",
        json={"provider": "bogus", "api_key": "x"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["stored"] is False
    assert "unknown provider" in body["note"]


def _seed_targets_and_projects(data_dir: Path, project_rows: list[tuple[str, str]]) -> None:
    """Seed agreed targets + a projects table with (id, status) rows so
    pending_first_move can be exercised."""
    import sqlite3, json as _json

    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(data_dir / "kompany.db"))
    conn.execute("CREATE TABLE IF NOT EXISTS company_config (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS credential_vault (name TEXT PRIMARY KEY, ciphertext TEXT, updated_at TEXT)"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, status TEXT)")
    conn.execute("INSERT OR REPLACE INTO company_config VALUES ('template_id', 'blank')")
    conn.execute(
        "INSERT OR REPLACE INTO company_config VALUES ('targets.agreed', ?)",
        (_json.dumps({"initial_budget": 50.0, "revenue_target": 1000.0,
                      "customer_target": None, "deadline": "2026-12-31",
                      "source": "agreed"}),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO credential_vault VALUES ('anthropic_api_key', 'x', '2026-05-27')"
    )
    for pid, status in project_rows:
        conn.execute("INSERT OR REPLACE INTO projects VALUES (?, ?)", (pid, status))
    conn.commit()
    conn.close()


def test_pending_first_move_false_when_a_project_completed(
    client: TestClient, tmp_path: Path
) -> None:
    """Once a first directive has run (even to completion), leftover
    unpicked drafts must NOT drag the founder back to step 5 — they're
    live on the dashboard now. Regression for 2026-05-27."""
    data_dir = Path(tmp_path / "data")
    _seed_targets_and_projects(data_dir, [
        ("done1", "completed"),
        ("draftA", "draft"),
        ("draftB", "draft"),
    ])
    res = client.get("/onboarding/status")
    assert res.status_code == 200
    assert res.json()["pending_first_move"] is False


def test_pending_first_move_true_when_only_drafts(
    client: TestClient, tmp_path: Path
) -> None:
    data_dir = Path(tmp_path / "data")
    _seed_targets_and_projects(data_dir, [
        ("draftA", "draft"),
        ("draftB", "draft"),
        ("draftC", "draft"),
    ])
    res = client.get("/onboarding/status")
    assert res.json()["pending_first_move"] is True


def test_audit_recent_returns_chronological(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/audit/recent backfills the live timeline; it must return events
    oldest-first with the timeline-relevant fields."""
    monkeypatch.setenv("KOMPANY_VAULT_KEY", "C8WJOwHdhwcWnW2siGKVyEggFwVHe41ERKC1SFRgfJ8=")
    api_module.reset_engine()
    engine = api_module.get_engine()
    engine.audit.record("task.started", "first", agent_role="cro")
    engine.audit.record("task.completed", "second", agent_role="cro")

    res = client.get("/audit/recent?limit=10")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    actions = [e["action"] for e in body]
    # oldest-first: "first" precedes "second"
    assert actions.index("first") < actions.index("second")
    assert {"event_type", "action", "agent_role", "project_id"} <= set(body[0])


def test_health_returns_200_without_engine(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
