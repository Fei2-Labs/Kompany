"""OAuth-subscription credential layer (06-16-agentic-chat-engine P3).

Lets the NATIVE agentic loop run on a ChatGPT/Codex *subscription* instead
of a pay-per-token API key. First (and only) target: OpenAI Codex/ChatGPT
PKCE OAuth → the Codex backend (``chatgpt.com/backend-api``), where native
tool_use stays intact.

Two pieces, matching the OpenClaw credential/vehicle split:

* :mod:`kompany.llm.oauth.openai_pkce` — the provider-specific PKCE flow:
  build the auth URL, run a localhost callback server, exchange the code
  for tokens, refresh on expiry. All endpoints/constants are overridable
  via env/settings (research was NOT independently confirmed against
  OpenAI's own docs — see module TODOs).
* :mod:`kompany.llm.oauth.token_store` — a credential-vault-backed token
  sink with ``get`` / ``refresh`` / ``clear`` and auto-refresh on expiry.
"""

from __future__ import annotations

from kompany.llm.oauth.openai_pkce import (
    OpenAIOAuthConfig,
    OpenAIPKCEClient,
    OAuthTokens,
    PKCEChallenge,
    generate_pkce,
)
from kompany.llm.oauth.token_store import (
    OAUTH_VAULT_KEY,
    OAuthTokenStore,
)

__all__ = [
    "OAUTH_VAULT_KEY",
    "OAuthTokenStore",
    "OAuthTokens",
    "OpenAIOAuthConfig",
    "OpenAIPKCEClient",
    "PKCEChallenge",
    "generate_pkce",
]
