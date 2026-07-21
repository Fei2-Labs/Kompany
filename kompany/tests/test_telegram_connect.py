"""Telegram connect endpoint + credentials masking (07-20-settings-telegram-ui).

Mirrors the email/resend connect test pattern: monkeypatch urllib so no
real network call is made, drive the FastAPI app via TestClient, assert
the vault ends up with the right encrypted rows.
"""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest


pytestmark = pytest.mark.usefixtures("telegram_env")


@pytest.fixture
def telegram_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    monkeypatch.setenv("KOMPANY_VAULT_KEY", "C8WJOwHdhwcWnW2siGKVyEggFwVHe41ERKC1SFRgfJ8=")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")  # don't auto-start the worker
    from kompany.interfaces import api as api_module

    api_module.reset_engine()
    yield api_module
    api_module.reset_engine()


def _patch_getme(monkeypatch, payload, *, raise_http=None):
    """Replace urllib.request.urlopen so /getMe returns ``payload``.

    A non-None ``raise_http`` (HTTPError) is raised instead. Only the
    Telegram getMe URL is intercepted; other urlopen calls (none in this
    test path) would fall through.
    """
    import urllib.request

    real_request = urllib.request.Request

    def fake_urlopen(req, timeout=30):
        url = getattr(req, "full_url", "") if isinstance(req, real_request) else str(req)
        if "api.telegram.org" in url and "getMe" in url:
            if raise_http is not None:
                raise raise_http
            return io.BytesIO(json.dumps(payload).encode("utf-8"))
        raise AssertionError(f"unexpected urlopen: {url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_connect_telegram_verifies_then_stores(telegram_env, monkeypatch):
    """POST /integrations/telegram/connect calls getMe, stores both
    credentials in the vault, returns ok=True with the bot username."""
    from fastapi.testclient import TestClient

    _patch_getme(
        monkeypatch,
        {"ok": True, "result": {"id": 42, "username": "kompany_bot"}},
    )
    client = TestClient(telegram_env.app)

    r = client.post(
        "/integrations/telegram/connect",
        json={"bot_token": "123:abc", "allowed_chat_ids": "111,-100222"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "@kompany_bot" in body["detail"]

    eng = telegram_env.get_engine()
    assert eng.credentials.get("telegram_bot_token") == "123:abc"
    assert eng.credentials.get("telegram_allowed_chat_ids") == "111,-100222"


def test_connect_telegram_rejects_bad_token(telegram_env, monkeypatch):
    """A 401 from getMe returns ok=False and writes nothing to the vault."""
    from fastapi.testclient import TestClient

    _patch_getme(
        monkeypatch,
        None,
        raise_http=HTTPError(
            url="https://api.telegram.org/botxxx/getMe",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"ok":false}'),
        ),
    )
    client = TestClient(telegram_env.app)

    r = client.post(
        "/integrations/telegram/connect",
        json={"bot_token": "bad", "allowed_chat_ids": "111"},
    )
    body = r.json()
    assert body["ok"] is False
    assert "401" in body["detail"]

    eng = telegram_env.get_engine()
    assert eng.credentials.get("telegram_bot_token") is None
    assert eng.credentials.get("telegram_allowed_chat_ids") is None


def test_connect_telegram_empty_token_keeps_saved(telegram_env, monkeypatch):
    """Empty bot_token means "keep the saved token" — verifies the saved
    one, updates only allowed_chat_ids. Mirrors the Resend pattern."""
    from fastapi.testclient import TestClient

    eng = telegram_env.get_engine()
    eng.credentials.set("telegram_bot_token", "saved:token")
    eng.credentials.set("telegram_allowed_chat_ids", "old")

    _patch_getme(
        monkeypatch,
        {"ok": True, "result": {"id": 42, "username": "kompany_bot"}},
    )
    client = TestClient(telegram_env.app)

    r = client.post(
        "/integrations/telegram/connect",
        json={"bot_token": "", "allowed_chat_ids": "new"},
    )
    body = r.json()
    assert body["ok"] is True
    assert eng.credentials.get("telegram_bot_token") == "saved:token"
    assert eng.credentials.get("telegram_allowed_chat_ids") == "new"


def test_get_integration_credentials_telegram_masks_token(telegram_env):
    """GET /integrations/telegram/credentials masks the bot token and
    returns allowed_chat_ids in clear — same shape as email/resend."""
    from fastapi.testclient import TestClient

    eng = telegram_env.get_engine()
    eng.credentials.set("telegram_bot_token", "123456:ABC-DEF")
    eng.credentials.set("telegram_allowed_chat_ids", "111,-100222")

    client = TestClient(telegram_env.app)
    r = client.get("/integrations/telegram/credentials")
    assert r.status_code == 200
    body = r.json()
    assert body["telegram_bot_token_set"] is True
    assert body["telegram_bot_token_mask"].endswith("-DEF")
    assert body["telegram_allowed_chat_ids"] == "111,-100222"


def test_get_integration_credentials_unknown_id_404(telegram_env):
    from fastapi.testclient import TestClient

    client = TestClient(telegram_env.app)
    r = client.get("/integrations/nope/credentials")
    assert r.status_code == 404
