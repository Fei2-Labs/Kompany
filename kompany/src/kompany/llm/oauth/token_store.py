"""OAuth token sink (06-16-agentic-chat-engine P3).

The single canonical store for OpenAI Codex/ChatGPT OAuth tokens, backed by
the engine's existing encrypted :class:`~kompany.state.credentials.CredentialVaultStore`
(Fernet-encrypted SQLite rows). Holds get / set / refresh / clear and
auto-refreshes when the access token has expired (research §5: "the store
owns refresh, providers register their own refresh hook").

Per the OpenClaw "token sink" lesson (research §1): read from ONE place,
keep the refresh-token rotation here, so Kompany and the founder's own Codex
CLI don't fight over invalidated refresh tokens.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from kompany.llm.oauth.openai_pkce import (
    OAuthTokens,
    OpenAIOAuthConfig,
    OpenAIPKCEClient,
)

log = logging.getLogger(__name__)

# Vault row name for the OpenAI OAuth token blob (must be in
# ``credentials.ALLOWED_CREDENTIALS``).
OAUTH_VAULT_KEY = "openai_oauth_tokens"


class OAuthTokenStore:
    """get / set / refresh / clear for OpenAI OAuth tokens.

    ``vault`` is any object exposing ``get(name)`` / ``set(name, value)`` /
    ``delete(name)`` — the engine passes its
    :class:`CredentialVaultStore`; tests pass a tiny in-memory fake.

    ``pkce_client`` performs the actual refresh network call; injected so
    refresh is unit-testable without a live token endpoint.
    """

    def __init__(
        self,
        vault: Any,
        pkce_client: OpenAIPKCEClient | None = None,
        *,
        vault_key: str = OAUTH_VAULT_KEY,
    ) -> None:
        self.vault = vault
        self.vault_key = vault_key
        self.pkce_client = pkce_client or OpenAIPKCEClient(
            config=OpenAIOAuthConfig.from_env()
        )
        self._lock = threading.Lock()

    # -- raw persistence ----------------------------------------------------

    def load(self) -> OAuthTokens | None:
        """Read the stored token set, or ``None`` when not logged in."""
        raw = self.vault.get(self.vault_key)
        if not raw:
            return None
        try:
            return OAuthTokens.from_dict(json.loads(raw))
        except (ValueError, KeyError, TypeError):
            log.warning("oauth token store: corrupt blob; treating as logged-out")
            return None

    def save(self, tokens: OAuthTokens) -> None:
        """Persist the token set (encrypted by the vault)."""
        self.vault.set(self.vault_key, json.dumps(tokens.to_dict()))

    def clear(self) -> None:
        """Log out — delete the stored token set."""
        self.vault.delete(self.vault_key)

    # -- access with auto-refresh ------------------------------------------

    def get_access_token(self) -> str | None:
        """Return a VALID access token, refreshing transparently if expired.

        ``None`` when there is no stored login. Raises only if a refresh is
        required but fails (the caller surfaces a re-login prompt).
        """
        tokens = self.get_tokens()
        return tokens.access_token if tokens else None

    def get_tokens(self, *, force_refresh: bool = False) -> OAuthTokens | None:
        """Return valid tokens (auto-refresh on expiry), or ``None``.

        Refresh is guarded by a lock so concurrent callers don't double-spend
        the rotating refresh token.
        """
        tokens = self.load()
        if tokens is None:
            return None
        if not force_refresh and not tokens.is_expired():
            return tokens
        with self._lock:
            # Re-read under the lock: another thread may have just refreshed.
            tokens = self.load()
            if tokens is None:
                return None
            if not force_refresh and not tokens.is_expired():
                return tokens
            return self._refresh_locked(tokens)

    def refresh(self) -> OAuthTokens | None:
        """Force a refresh now; returns the new tokens or ``None`` if logged out."""
        return self.get_tokens(force_refresh=True)

    def _refresh_locked(self, tokens: OAuthTokens) -> OAuthTokens:
        if not tokens.refresh_token:
            raise OAuthTokenStoreError(
                "access token expired and no refresh token is stored — "
                "re-run `kompany auth openai` to log in again."
            )
        new_tokens = self.pkce_client.refresh(tokens.refresh_token)
        # Preserve the account id if the refresh response didn't carry one.
        if not new_tokens.account_id:
            new_tokens.account_id = tokens.account_id
        self.save(new_tokens)
        return new_tokens

    # -- status ------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Founder-facing status: logged in? expired? account id?"""
        tokens = self.load()
        if tokens is None:
            return {"logged_in": False}
        return {
            "logged_in": True,
            "expired": tokens.is_expired(),
            "expires_at": tokens.expires_at,
            "account_id": tokens.account_id,
            "has_refresh_token": bool(tokens.refresh_token),
        }


class OAuthTokenStoreError(RuntimeError):
    """Raised when a valid token can't be produced (e.g. refresh failed)."""


__all__ = [
    "OAUTH_VAULT_KEY",
    "OAuthTokenStore",
    "OAuthTokenStoreError",
]
