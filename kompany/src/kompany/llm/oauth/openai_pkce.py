"""OpenAI Codex/ChatGPT PKCE OAuth flow (06-16-agentic-chat-engine P3).

Mechanics follow ``research/subscription-oauth-and-openclaw.md`` §2:

1. generate a PKCE verifier/challenge + random ``state``
2. open ``https://auth.openai.com/oauth/authorize?...``
3. capture the callback on ``http://127.0.0.1:1455/auth/callback``
4. exchange the code at ``https://auth.openai.com/oauth/token``
5. extract ``accountId`` from the access token (a JWT)
6. hand ``{access, refresh, expires, account_id}`` to the token store

EVERY endpoint / client_id / port is overridable via :class:`OpenAIOAuthConfig`
(env-backed) because the research doc explicitly flags these as taken from
OpenClaw's docs and NOT independently confirmed against OpenAI. See the
``# TODO(verify-live)`` markers — they must be checked against a real Codex
login before relying on the constants.

Network + browser + the callback server are all injectable so the flow can
be unit-tested with fakes (no real network in tests).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

log = logging.getLogger(__name__)


# --- Overridable constants (research doc §2; NOT confirmed vs OpenAI) -------
#
# TODO(verify-live): the four constants below come from OpenClaw's
# ``docs/concepts/oauth.md`` (cited in research/subscription-oauth-and-openclaw.md
# §2 + Caveats). They were NOT independently confirmed against OpenAI's own
# docs. Verify each against a real Codex/ChatGPT login before shipping; all
# are overridable via env so a fix needs no code change.
_DEFAULT_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
_DEFAULT_TOKEN_URL = "https://auth.openai.com/oauth/token"
# The Codex CLI's public OAuth client id. TODO(verify-live): confirm the
# real client id used by `codex login`; this placeholder MUST be replaced
# (or set via KOMPANY_OPENAI_OAUTH_CLIENT_ID) before a live login.
_DEFAULT_CLIENT_ID = "app_codex_cli"
_DEFAULT_CALLBACK_HOST = "127.0.0.1"
_DEFAULT_CALLBACK_PORT = 1455
_DEFAULT_CALLBACK_PATH = "/auth/callback"
# Scopes the Codex OAuth app expects. TODO(verify-live): confirm scope list.
_DEFAULT_SCOPE = "openid profile email offline_access"

# Refresh this many seconds BEFORE the real expiry so an in-flight request
# never races the boundary.
_EXPIRY_SKEW_SECONDS = 120


@dataclass
class OpenAIOAuthConfig:
    """Endpoints/constants for the OpenAI PKCE flow, env-overridable.

    Use :meth:`from_env` to pull overrides from the process environment so
    a founder (or a live-login fix) can correct any unconfirmed constant
    without a code change.
    """

    client_id: str = _DEFAULT_CLIENT_ID
    authorize_url: str = _DEFAULT_AUTHORIZE_URL
    token_url: str = _DEFAULT_TOKEN_URL
    callback_host: str = _DEFAULT_CALLBACK_HOST
    callback_port: int = _DEFAULT_CALLBACK_PORT
    callback_path: str = _DEFAULT_CALLBACK_PATH
    scope: str = _DEFAULT_SCOPE

    @property
    def redirect_uri(self) -> str:
        return (
            f"http://{self.callback_host}:{self.callback_port}{self.callback_path}"
        )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "OpenAIOAuthConfig":
        env = env if env is not None else dict(os.environ)
        port_raw = env.get("KOMPANY_OPENAI_OAUTH_CALLBACK_PORT")
        try:
            port = int(port_raw) if port_raw else _DEFAULT_CALLBACK_PORT
        except ValueError:
            port = _DEFAULT_CALLBACK_PORT
        return cls(
            client_id=env.get("KOMPANY_OPENAI_OAUTH_CLIENT_ID", _DEFAULT_CLIENT_ID),
            authorize_url=env.get(
                "KOMPANY_OPENAI_OAUTH_AUTHORIZE_URL", _DEFAULT_AUTHORIZE_URL
            ),
            token_url=env.get("KOMPANY_OPENAI_OAUTH_TOKEN_URL", _DEFAULT_TOKEN_URL),
            callback_host=env.get(
                "KOMPANY_OPENAI_OAUTH_CALLBACK_HOST", _DEFAULT_CALLBACK_HOST
            ),
            callback_port=port,
            callback_path=env.get(
                "KOMPANY_OPENAI_OAUTH_CALLBACK_PATH", _DEFAULT_CALLBACK_PATH
            ),
            scope=env.get("KOMPANY_OPENAI_OAUTH_SCOPE", _DEFAULT_SCOPE),
        )


@dataclass
class PKCEChallenge:
    """A PKCE verifier/challenge/state triple for one auth attempt."""

    verifier: str
    challenge: str
    state: str
    method: str = "S256"


@dataclass
class OAuthTokens:
    """Stored OAuth credential set (the "token sink" row)."""

    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds
    account_id: str | None = None
    token_type: str = "Bearer"
    scope: str = ""

    def is_expired(self, *, skew_seconds: float = _EXPIRY_SKEW_SECONDS,
                   now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return now >= (self.expires_at - skew_seconds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "account_id": self.account_id,
            "token_type": self.token_type,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OAuthTokens":
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=str(data.get("refresh_token", "")),
            expires_at=float(data.get("expires_at", 0.0)),
            account_id=data.get("account_id"),
            token_type=str(data.get("token_type", "Bearer")),
            scope=str(data.get("scope", "")),
        )


def generate_pkce(*, _rng: Callable[[int], str] | None = None) -> PKCEChallenge:
    """Build a fresh PKCE verifier/challenge + ``state`` (RFC 7636, S256).

    ``_rng`` is injectable for deterministic tests; it must return a
    URL-safe token of at least the requested byte length.
    """
    rng = _rng or (lambda n: secrets.token_urlsafe(n))
    verifier = rng(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    state = rng(24)
    return PKCEChallenge(verifier=verifier, challenge=challenge, state=state)


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Best-effort decode of a JWT payload (no signature verification).

    Used only to read the account id out of the access token (research §2
    step 5). Returns ``{}`` on any malformed token — the account id is
    optional metadata, never a security boundary here.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(payload + padding)
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {}
    except Exception:
        return {}


def extract_account_id(access_token: str) -> str | None:
    """Pull the ChatGPT account id from the access-token JWT (research §2).

    TODO(verify-live): the exact claim path is unconfirmed. We probe the
    common shapes (``chatgpt_account_id``, nested
    ``https://api.openai.com/auth`` org/account claims, plain ``account_id``
    / ``sub``). Verify against a real token's claims.
    """
    claims = _decode_jwt_payload(access_token)
    if not claims:
        return None
    for key in ("chatgpt_account_id", "account_id"):
        val = claims.get(key)
        if val:
            return str(val)
    auth = claims.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        for key in ("chatgpt_account_id", "account_id", "organization_id"):
            val = auth.get(key)
            if val:
                return str(val)
    sub = claims.get("sub")
    return str(sub) if sub else None


# --- HTTP transport (injectable) -------------------------------------------


def _default_http_post(url: str, data: dict[str, str],
                       timeout: float = 30.0) -> dict[str, Any]:
    """POST ``application/x-www-form-urlencoded`` and parse a JSON reply.

    The single real-network touchpoint of this module. Injected as a fake
    in tests so no live call is ever made.
    """
    body = urllib.parse.urlencode(data).encode("ascii")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


HTTPPostFn = Callable[[str, dict[str, str]], dict[str, Any]]
BrowserOpenFn = Callable[[str], Any]


# --- Localhost callback server ---------------------------------------------


class _CallbackResult:
    def __init__(self) -> None:
        self.code: str | None = None
        self.state: str | None = None
        self.error: str | None = None
        self.event = threading.Event()


def _make_handler(result: _CallbackResult, expect_path: str):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # silence stdlib logging
            pass

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != expect_path:
                self.send_response(404)
                self.end_headers()
                return
            params = urllib.parse.parse_qs(parsed.query)
            result.code = (params.get("code") or [None])[0]
            result.state = (params.get("state") or [None])[0]
            result.error = (params.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Kompany: login received.</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
            result.event.set()

    return _Handler


def run_callback_server(
    config: OpenAIOAuthConfig,
    *,
    timeout: float = 300.0,
) -> _CallbackResult:
    """Serve exactly one OAuth redirect on the configured localhost port.

    Blocks (up to ``timeout``) until the browser hits the callback path,
    then returns the parsed ``code``/``state``/``error``. Lives behind the
    injectable ``callback_server`` hook on :class:`OpenAIPKCEClient` so the
    flow is testable without a real socket.
    """
    result = _CallbackResult()
    handler = _make_handler(result, config.callback_path)
    server = HTTPServer((config.callback_host, config.callback_port), handler)
    server.timeout = timeout

    def _serve() -> None:
        while not result.event.is_set():
            server.handle_request()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    result.event.wait(timeout=timeout)
    try:
        server.server_close()
    except Exception:
        pass
    return result


@dataclass
class OpenAIPKCEClient:
    """Drives the OpenAI Codex/ChatGPT PKCE OAuth flow.

    All side-effecting dependencies are injectable so the interactive flow
    is unit-tested with fakes:

    * ``http_post`` — token-exchange/refresh transport (default: urllib)
    * ``browser_open`` — opens the auth URL (default: ``webbrowser.open``)
    * ``callback_server`` — runs the localhost redirect catcher
      (default: :func:`run_callback_server`)
    """

    config: OpenAIOAuthConfig = field(default_factory=OpenAIOAuthConfig)
    http_post: HTTPPostFn = _default_http_post
    browser_open: BrowserOpenFn | None = None
    callback_server: Callable[[OpenAIOAuthConfig], _CallbackResult] | None = None
    clock: Callable[[], float] = time.time

    def build_auth_url(self, pkce: PKCEChallenge) -> str:
        """Build the ``/oauth/authorize`` URL for a PKCE attempt (research §2.2)."""
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": self.config.scope,
            "code_challenge": pkce.challenge,
            "code_challenge_method": pkce.method,
            "state": pkce.state,
        }
        return f"{self.config.authorize_url}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, pkce: PKCEChallenge) -> OAuthTokens:
        """Exchange an authorization ``code`` for tokens (research §2.4)."""
        data = {
            "grant_type": "authorization_code",
            "client_id": self.config.client_id,
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "code_verifier": pkce.verifier,
        }
        return self._tokens_from_response(self.http_post(self.config.token_url, data))

    def refresh(self, refresh_token: str) -> OAuthTokens:
        """Rotate an expired access token using the refresh token (research §2)."""
        data = {
            "grant_type": "refresh_token",
            "client_id": self.config.client_id,
            "refresh_token": refresh_token,
        }
        tokens = self._tokens_from_response(
            self.http_post(self.config.token_url, data)
        )
        # OAuth servers may omit a rotated refresh token; keep the old one.
        if not tokens.refresh_token:
            tokens.refresh_token = refresh_token
        return tokens

    def login_interactive(self, *, timeout: float = 300.0) -> OAuthTokens:
        """Full PKCE login: open browser, catch callback, exchange code.

        Returns the freshly minted :class:`OAuthTokens`. Raises on a
        state mismatch (CSRF guard), an OAuth ``error`` redirect, or a
        missing code. The browser/callback/network bits are all injected,
        so the loop itself is exercisable in tests; only the LIVE flow
        needs a real founder login.
        """
        pkce = generate_pkce()
        auth_url = self.build_auth_url(pkce)
        opener = self.browser_open or _default_browser_open
        opener(auth_url)
        server = self.callback_server or run_callback_server
        result = server(self.config)
        if result.error:
            raise OAuthLoginError(f"OAuth provider returned error: {result.error}")
        if not result.code:
            raise OAuthLoginError(
                "No authorization code received on the localhost callback "
                f"({self.config.redirect_uri}) within {timeout:.0f}s."
            )
        if result.state != pkce.state:
            raise OAuthLoginError(
                "OAuth state mismatch — possible CSRF; aborting login."
            )
        return self.exchange_code(result.code, pkce)

    def _tokens_from_response(self, payload: dict[str, Any]) -> OAuthTokens:
        if "error" in payload:
            raise OAuthLoginError(
                f"token endpoint error: {payload.get('error')} "
                f"{payload.get('error_description', '')}".strip()
            )
        access = payload.get("access_token")
        if not access:
            raise OAuthLoginError("token response missing access_token")
        expires_in = float(payload.get("expires_in", 3600))
        return OAuthTokens(
            access_token=str(access),
            refresh_token=str(payload.get("refresh_token", "")),
            expires_at=self.clock() + expires_in,
            account_id=extract_account_id(str(access)),
            token_type=str(payload.get("token_type", "Bearer")),
            scope=str(payload.get("scope", self.config.scope)),
        )


class OAuthLoginError(RuntimeError):
    """Raised for any failure in the OAuth login/exchange/refresh flow."""


def _default_browser_open(url: str) -> None:
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:  # pragma: no cover — headless box; founder pastes URL
        log.info("Open this URL to authorize Kompany:\n%s", url)


__all__ = [
    "OAuthLoginError",
    "OAuthTokens",
    "OpenAIOAuthConfig",
    "OpenAIPKCEClient",
    "PKCEChallenge",
    "extract_account_id",
    "generate_pkce",
    "run_callback_server",
]
