"""Unit tests for ``POST /onboarding/ping`` — statelessness invariants.

The ping endpoint is documented as the sole exception to the
cost-visibility discipline: it MUST NOT write a ledger row, an audit
row, or any persistent record into the founder's data directory. These
tests pin that invariant.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kompany.installer.onboard import PROVIDER_ENV_VARS
from kompany.interfaces import api as api_module


@pytest.fixture(autouse=True)
def _isolate_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    for var in PROVIDER_ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(data_dir))
    # Make sure no engine is held over from a previous test.
    api_module.reset_engine()
    return data_dir


@pytest.fixture
def client() -> TestClient:
    api_module.reset_engine()
    return TestClient(api_module.app)


def _seed_db_with_known_state(data_dir: Path) -> dict[str, int]:
    """Create the founder DB and capture baseline row counts."""
    from kompany.state.database import Database

    data_dir.mkdir(parents=True, exist_ok=True)
    Database(data_dir)
    db_path = data_dir / "kompany.db"
    assert db_path.exists()

    conn = sqlite3.connect(str(db_path))
    counts = {
        "ledger": conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0],
        "audit_log": conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        "credential_vault": conn.execute(
            "SELECT COUNT(*) FROM credential_vault"
        ).fetchone()[0],
        "company_config": conn.execute(
            "SELECT COUNT(*) FROM company_config"
        ).fetchone()[0],
        "health_events": conn.execute(
            "SELECT COUNT(*) FROM health_events"
        ).fetchone()[0],
    }
    conn.close()
    return counts


def _current_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    counts = {
        "ledger": conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0],
        "audit_log": conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        "credential_vault": conn.execute(
            "SELECT COUNT(*) FROM credential_vault"
        ).fetchone()[0],
        "company_config": conn.execute(
            "SELECT COUNT(*) FROM company_config"
        ).fetchone()[0],
        "health_events": conn.execute(
            "SELECT COUNT(*) FROM health_events"
        ).fetchone()[0],
    }
    conn.close()
    return counts


def test_ping_endpoint_does_not_write_to_founder_db(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _isolate_env: Path,
) -> None:
    """Successful ping leaves the founder DB row counts unchanged."""
    data_dir = _isolate_env
    before = _seed_db_with_known_state(data_dir)
    db_path = data_dir / "kompany.db"

    def fake_ping(provider: str, api_key: str, **kwargs: Any) -> tuple[bool, str]:
        return True, "ok"

    monkeypatch.setattr(
        "kompany.installer.onboard._ping_llm", fake_ping
    )

    res = client.post(
        "/onboarding/ping",
        json={"provider": "anthropic", "api_key": "sk-ant-fake"},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True

    after = _current_counts(db_path)
    assert after == before, (
        f"ping should not mutate the founder DB; before={before} after={after}"
    )


def test_ping_endpoint_does_not_write_on_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _isolate_env: Path,
) -> None:
    """Even on auth failure no row is written to ledger / audit / vault."""
    data_dir = _isolate_env
    before = _seed_db_with_known_state(data_dir)
    db_path = data_dir / "kompany.db"

    def fake_ping(provider: str, api_key: str, **kwargs: Any) -> tuple[bool, str]:
        return False, "AuthenticationError: 401 invalid x-api-key"

    monkeypatch.setattr(
        "kompany.installer.onboard._ping_llm", fake_ping
    )

    res = client.post(
        "/onboarding/ping",
        json={"provider": "anthropic", "api_key": "sk-bad"},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert res.json()["error_code"] == "unauthorized"

    after = _current_counts(db_path)
    assert after == before, (
        f"failed ping should still be stateless; before={before} after={after}"
    )


def test_ping_endpoint_does_not_create_founder_db_when_absent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _isolate_env: Path,
) -> None:
    """On a fresh install with no DB, ping must not bootstrap one.

    This pins the statelessness invariant against accidental engine
    spin-up inside the ping handler — the founder DB should only come
    into existence via the dedicated onboard / install paths.
    """
    data_dir = _isolate_env
    db_path = data_dir / "kompany.db"
    assert not db_path.exists()

    def fake_ping(provider: str, api_key: str, **kwargs: Any) -> tuple[bool, str]:
        return True, "ok"

    monkeypatch.setattr(
        "kompany.installer.onboard._ping_llm", fake_ping
    )

    res = client.post(
        "/onboarding/ping",
        json={"provider": "anthropic", "api_key": "sk-ant-fake"},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True
    # No founder DB should have been created as a side effect.
    assert not db_path.exists(), (
        "ping handler must not create kompany.db on a fresh install"
    )
