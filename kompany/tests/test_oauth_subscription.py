"""OAuth-subscription credential layer (06-16-agentic-chat-engine P3).

Covers PKCE generation, auth-URL build, token exchange/refresh (mock HTTP),
the vault-backed token store get/refresh/expiry, the chatgpt-oauth provider
dispatch (bearer token + tools=), the model_source kind→native-vehicle
mapping, and subscription shadow-billing for the oauth prefix.

NO real network or browser calls — every side effect is injected as a fake.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

import pytest

from kompany.config.model_source import ModelSource
from kompany.llm.oauth.openai_pkce import (
    OAuthLoginError,
    OAuthTokens,
    OpenAIOAuthConfig,
    OpenAIPKCEClient,
    extract_account_id,
    generate_pkce,
)
from kompany.llm.oauth.token_store import (
    OAUTH_VAULT_KEY,
    OAuthTokenStore,
    OAuthTokenStoreError,
)
from kompany.llm.providers import Provider, detect_provider


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeVault:
    """In-memory stand-in for CredentialVaultStore (get/set/delete)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.store.get(name)

    def set(self, name: str, value: str):
        self.store[name] = value

    def delete(self, name: str) -> dict:
        existed = name in self.store
        self.store.pop(name, None)
        return {"name": name, "deleted": existed}


def _jwt(payload: dict) -> str:
    def b64(obj) -> str:
        raw = json.dumps(obj).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64(payload)}.sig"


# --------------------------------------------------------------------------
# PKCE generation + auth URL
# --------------------------------------------------------------------------


def test_generate_pkce_challenge_is_s256_of_verifier():
    pkce = generate_pkce()
    expected = (
        base64.urlsafe_b64encode(
            hashlib.sha256(pkce.verifier.encode("ascii")).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    assert pkce.challenge == expected
    assert pkce.method == "S256"
    assert "=" not in pkce.challenge  # no padding
    assert pkce.state and pkce.verifier


def test_generate_pkce_is_random_each_call():
    a, b = generate_pkce(), generate_pkce()
    assert a.verifier != b.verifier
    assert a.state != b.state


def test_build_auth_url_contains_pkce_and_redirect():
    client = OpenAIPKCEClient(config=OpenAIOAuthConfig(client_id="cid-123"))
    pkce = generate_pkce()
    url = client.build_auth_url(pkce)
    assert url.startswith("https://auth.openai.com/oauth/authorize?")
    assert "client_id=cid-123" in url
    assert f"code_challenge={pkce.challenge}" in url
    assert "code_challenge_method=S256" in url
    assert f"state={pkce.state}" in url
    assert "response_type=code" in url
    assert "127.0.0.1%3A1455" in url  # redirect_uri encoded


def test_config_from_env_overrides_constants():
    cfg = OpenAIOAuthConfig.from_env({
        "KOMPANY_OPENAI_OAUTH_CLIENT_ID": "env-client",
        "KOMPANY_OPENAI_OAUTH_TOKEN_URL": "https://example.test/token",
        "KOMPANY_OPENAI_OAUTH_CALLBACK_PORT": "9999",
    })
    assert cfg.client_id == "env-client"
    assert cfg.token_url == "https://example.test/token"
    assert cfg.callback_port == 9999
    assert cfg.redirect_uri == "http://127.0.0.1:9999/auth/callback"


# --------------------------------------------------------------------------
# Token exchange + refresh (mock HTTP)
# --------------------------------------------------------------------------


def test_exchange_code_builds_request_and_parses_tokens():
    captured = {}

    def fake_post(url, data):
        captured["url"] = url
        captured["data"] = data
        return {
            "access_token": _jwt({"chatgpt_account_id": "acct-9"}),
            "refresh_token": "refresh-abc",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    client = OpenAIPKCEClient(
        config=OpenAIOAuthConfig(client_id="cid"),
        http_post=fake_post,
        clock=lambda: 1000.0,
    )
    pkce = generate_pkce()
    tokens = client.exchange_code("the-code", pkce)

    assert captured["url"] == "https://auth.openai.com/oauth/token"
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["code"] == "the-code"
    assert captured["data"]["code_verifier"] == pkce.verifier
    assert captured["data"]["client_id"] == "cid"
    assert tokens.refresh_token == "refresh-abc"
    assert tokens.expires_at == 1000.0 + 3600
    assert tokens.account_id == "acct-9"


def test_refresh_keeps_old_refresh_token_when_not_rotated():
    def fake_post(url, data):
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "old-refresh"
        return {"access_token": _jwt({"sub": "u1"}), "expires_in": 100}

    client = OpenAIPKCEClient(http_post=fake_post, clock=lambda: 0.0)
    tokens = client.refresh("old-refresh")
    assert tokens.refresh_token == "old-refresh"  # preserved
    assert tokens.account_id == "u1"


def test_token_endpoint_error_raises():
    client = OpenAIPKCEClient(
        http_post=lambda url, data: {"error": "invalid_grant"}
    )
    with pytest.raises(OAuthLoginError):
        client.exchange_code("bad", generate_pkce())


def test_extract_account_id_probes_nested_claim():
    tok = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "nested-1"}})
    assert extract_account_id(tok) == "nested-1"
    assert extract_account_id("not-a-jwt") is None


# --------------------------------------------------------------------------
# Interactive login (fakes for browser + callback)
# --------------------------------------------------------------------------


def _login_client(callback_result, *, post=None):
    cfg = OpenAIOAuthConfig(client_id="cid")
    opened = {}

    def fake_browser(url):
        opened["url"] = url

    def fake_server(config):
        return callback_result

    client = OpenAIPKCEClient(
        config=cfg,
        http_post=post or (lambda url, data: {
            "access_token": _jwt({"sub": "x"}),
            "refresh_token": "r",
            "expires_in": 60,
        }),
        browser_open=fake_browser,
        callback_server=fake_server,
        clock=lambda: 0.0,
    )
    return client, opened


class _CB:
    def __init__(self, code=None, state=None, error=None):
        self.code, self.state, self.error = code, state, error


def test_login_interactive_state_mismatch_raises(monkeypatch):
    # Force a deterministic pkce state, then return a different one.
    monkeypatch.setattr(
        "kompany.llm.oauth.openai_pkce.generate_pkce",
        lambda **_: __import__(
            "kompany.llm.oauth.openai_pkce", fromlist=["PKCEChallenge"]
        ).PKCEChallenge(verifier="v", challenge="c", state="EXPECTED"),
    )
    client, _ = _login_client(_CB(code="code", state="WRONG"))
    with pytest.raises(OAuthLoginError, match="state mismatch"):
        client.login_interactive()


def test_login_interactive_success_exchanges_code(monkeypatch):
    import kompany.llm.oauth.openai_pkce as mod

    monkeypatch.setattr(
        mod,
        "generate_pkce",
        lambda **_: mod.PKCEChallenge(verifier="v", challenge="c", state="S"),
    )
    client, opened = _login_client(_CB(code="auth-code", state="S"))
    tokens = client.login_interactive()
    assert opened["url"].startswith("https://auth.openai.com/oauth/authorize")
    assert tokens.refresh_token == "r"


def test_login_interactive_error_redirect_raises(monkeypatch):
    import kompany.llm.oauth.openai_pkce as mod

    monkeypatch.setattr(
        mod,
        "generate_pkce",
        lambda **_: mod.PKCEChallenge(verifier="v", challenge="c", state="S"),
    )
    client, _ = _login_client(_CB(error="access_denied"))
    with pytest.raises(OAuthLoginError, match="access_denied"):
        client.login_interactive()


# --------------------------------------------------------------------------
# Token store: get / refresh / expiry / clear
# --------------------------------------------------------------------------


def _store_with_tokens(tokens, pkce_client=None):
    vault = FakeVault()
    store = OAuthTokenStore(vault, pkce_client=pkce_client)
    store.save(tokens)
    return vault, store


def test_token_store_save_load_roundtrip():
    vault, store = _store_with_tokens(
        OAuthTokens(access_token="a", refresh_token="r",
                    expires_at=time.time() + 1000, account_id="acct")
    )
    assert OAUTH_VAULT_KEY in vault.store
    loaded = store.load()
    assert loaded.access_token == "a"
    assert loaded.account_id == "acct"


def test_token_store_returns_token_when_valid():
    _, store = _store_with_tokens(
        OAuthTokens(access_token="valid", refresh_token="r",
                    expires_at=time.time() + 9999)
    )
    assert store.get_access_token() == "valid"


def test_token_store_auto_refreshes_when_expired():
    refresh_calls = {"n": 0}

    class FakePKCE:
        def refresh(self, refresh_token):
            refresh_calls["n"] += 1
            assert refresh_token == "r-old"
            return OAuthTokens(
                access_token="fresh", refresh_token="r-new",
                expires_at=time.time() + 3600, account_id=None,
            )

    vault, store = _store_with_tokens(
        OAuthTokens(access_token="stale", refresh_token="r-old",
                    expires_at=time.time() - 10, account_id="keep-me"),
        pkce_client=FakePKCE(),
    )
    token = store.get_access_token()
    assert token == "fresh"
    assert refresh_calls["n"] == 1
    # account id preserved across refresh; new token persisted
    assert store.load().account_id == "keep-me"
    assert store.load().access_token == "fresh"


def test_token_store_refresh_without_refresh_token_raises():
    class FakePKCE:
        def refresh(self, refresh_token):  # pragma: no cover - shouldn't run
            raise AssertionError("should not call refresh")

    _, store = _store_with_tokens(
        OAuthTokens(access_token="stale", refresh_token="",
                    expires_at=time.time() - 10),
        pkce_client=FakePKCE(),
    )
    with pytest.raises(OAuthTokenStoreError):
        store.get_access_token()


def test_token_store_status_and_clear():
    vault, store = _store_with_tokens(
        OAuthTokens(access_token="a", refresh_token="r",
                    expires_at=time.time() + 100, account_id="z")
    )
    status = store.status()
    assert status["logged_in"] is True
    assert status["account_id"] == "z"
    store.clear()
    assert store.load() is None
    assert store.status() == {"logged_in": False}


# --------------------------------------------------------------------------
# Provider dispatch over the OAuth bearer token
# --------------------------------------------------------------------------


def test_chatgpt_oauth_prefix_detected():
    assert detect_provider("chatgpt-oauth:gpt-5") == Provider.CHATGPT_OAUTH
    # codex CLI prefix still resolves to the CLI, not the oauth path
    assert detect_provider("codex:gpt-5") == Provider.CODEX_CLI


class _FakeOpenAIResponse:
    def __init__(self):
        self.choices = [
            type("C", (), {
                "message": type("M", (), {
                    "content": "hi", "tool_calls": None
                })(),
                "finish_reason": "stop",
            })()
        ]
        self.usage = type("U", (), {"prompt_tokens": 5, "completion_tokens": 3})()


class _FakeOpenAIClient:
    def __init__(self):
        self.created_kwargs = None

        class _Completions:
            def create(_self, **kwargs):
                self.created_kwargs = kwargs
                return _FakeOpenAIResponse()

        self.chat = type("Chat", (), {"completions": _Completions()})()


def _mixin_host(token_store, fake_client):
    """Minimal object exposing the ProviderMixin surface for dispatch tests."""
    from kompany.llm.client_parts._provider_mixin import ProviderMixin

    class Host(ProviderMixin):
        def __init__(self):
            self.settings = type("S", (), {"custom_base_url": ""})()
            self.silent_timeout_seconds = 0
            self._anthropic_client = None
            self._openai_clients = {}
            self.provider_error_handler = None
            self.oauth_token_store = token_store

        def _get_chatgpt_oauth_client(self):
            # capture that the bearer token was read, then return the fake
            assert token_store.get_access_token() == "bearer-xyz"
            return fake_client

    return Host()


def test_oauth_dispatch_strips_prefix_and_uses_bearer_client():
    from kompany.llm.providers import Provider as P

    vault = FakeVault()
    store = OAuthTokenStore(vault)
    store.save(OAuthTokens(access_token="bearer-xyz", refresh_token="r",
                           expires_at=time.time() + 9999))
    fake_client = _FakeOpenAIClient()
    host = _mixin_host(store, fake_client)

    resp = host._call_openai_compatible(
        P.CHATGPT_OAUTH, "chatgpt-oauth:gpt-5", "sys", "hello", 256
    )
    assert resp.text == "hi"
    # model prefix stripped to the bare id for the backend
    assert fake_client.created_kwargs["model"] == "gpt-5"


def test_oauth_dispatch_with_tools_sends_tools_and_bearer():
    from kompany.llm.client_parts._types import ToolSpec
    from kompany.llm.providers import Provider as P

    vault = FakeVault()
    store = OAuthTokenStore(vault)
    store.save(OAuthTokens(access_token="bearer-xyz", refresh_token="r",
                           expires_at=time.time() + 9999))
    fake_client = _FakeOpenAIClient()
    host = _mixin_host(store, fake_client)

    tool = ToolSpec(name="web_search", description="search the web")
    resp = host._call_openai_compatible_tools(
        P.CHATGPT_OAUTH, "chatgpt-oauth:gpt-5", "sys", [
            {"role": "user", "content": "research X"}
        ], [tool], 512, "auto",
    )
    kwargs = fake_client.created_kwargs
    assert kwargs["model"] == "gpt-5"
    assert kwargs["tools"] == [tool.to_openai()]
    assert kwargs["tool_choice"] == "auto"
    # The dispatch records the wire (bare) model on the response; the
    # ROUTING id (chatgpt-oauth:gpt-5) is what call_with_tools passes to the
    # cost tracker, which is what triggers shadow billing — see
    # test_cost_tracker_shadow_bills_oauth_call below.
    assert resp.model == "gpt-5"


def test_oauth_provider_supports_native_tools():
    from kompany.llm.client_parts._provider_mixin import (
        provider_supports_native_tools,
    )

    assert provider_supports_native_tools(Provider.CHATGPT_OAUTH) is True


def test_oauth_client_requires_login():
    from kompany.llm.client_parts._provider_mixin import ProviderMixin

    class Host(ProviderMixin):
        def __init__(self):
            self.oauth_token_store = OAuthTokenStore(FakeVault())  # empty

    with pytest.raises(RuntimeError, match="not logged in"):
        Host()._get_chatgpt_oauth_client()


def test_oauth_client_requires_wired_store():
    from kompany.llm.client_parts._provider_mixin import ProviderMixin

    class Host(ProviderMixin):
        def __init__(self):
            self.oauth_token_store = None

    with pytest.raises(RuntimeError, match="no OAuth token store"):
        Host()._get_chatgpt_oauth_client()


# --------------------------------------------------------------------------
# model_source kind → native vehicle + shadow billing
# --------------------------------------------------------------------------


def test_oauth_subscription_kind_maps_to_native_vehicle():
    source = ModelSource(kind="openai_oauth_subscription", monthly_fee_usd=20.0)
    assert source.vehicle == "native"
    assert source.billing_mode == "subscription"


def test_oauth_subscription_books_oauth_prefix_as_shadow():
    source = ModelSource(kind="openai_oauth_subscription", monthly_fee_usd=20.0)
    assert source.is_subscription_call("chatgpt-oauth:gpt-5") is True
    # a non-oauth model under this source is NOT a free subscription call
    assert source.is_subscription_call("gpt-5") is False


def test_oauth_subscription_requires_monthly_fee():
    with pytest.raises(ValueError, match="monthly_fee_usd"):
        ModelSource(kind="openai_oauth_subscription")


def test_select_runner_picks_native_for_oauth_subscription():
    from kompany.core.harness_execution import selection

    class FakeNativeRunner:
        pass

    source = ModelSource(kind="openai_oauth_subscription", monthly_fee_usd=20.0)
    settings = type("S", (), {
        "model_source": source,
        "harness_execution_enabled": True,
        "native_runner_enabled": True,
        "model_primary": "chatgpt-oauth:gpt-5",
    })()
    runner = selection.select_runner(settings, llm_client=object())
    # NativeRunner is the real class; just assert we got one and not None.
    from kompany.core.harness import NativeRunner
    assert isinstance(runner, NativeRunner)


def test_select_runner_oauth_falls_back_without_native_flag():
    from kompany.core.harness_execution import selection

    source = ModelSource(kind="openai_oauth_subscription", monthly_fee_usd=20.0)
    settings = type("S", (), {
        "model_source": source,
        "harness_execution_enabled": True,
        "native_runner_enabled": False,
        "model_primary": "chatgpt-oauth:gpt-5",
    })()
    assert selection.select_runner(settings, llm_client=object()) is None
    # also None when native flag on but no llm_client threaded
    settings.native_runner_enabled = True
    assert selection.select_runner(settings, llm_client=None) is None
