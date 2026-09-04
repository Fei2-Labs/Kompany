"""Environment scrubbing for subprocesses that run LLM-controlled code.

An agent's ``run_shell`` or a browser-automation node script inherits the
engine's process environment by default — including every provider API key,
vault key and token the founder configured. Anything the model (or a page it
visits) can make that subprocess print or exfiltrate becomes a credential
leak. :func:`scrubbed_env` returns a copy with secret-looking variables
removed; callers that legitimately need a secret pass it explicitly.
"""

from __future__ import annotations

import os
import re
from typing import Mapping

_SECRET_NAME = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|AUTH|PRIVATE|VAULT|SESSION|DSN|"
    r"CONNECTION_STRING|WEBHOOK)",
    re.IGNORECASE,
)
# Always removed regardless of name pattern.
_ALWAYS_DROP: frozenset[str] = frozenset({
    "KOMPANY_VAULT_KEY", "KOMPANY_DATA_DIR", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "GEMINI_API_KEY", "TAVILY_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "NPM_TOKEN", "TELEGRAM_BOT_TOKEN",
})
# Keep even though the name matches (harmless, often needed).
_KEEP: frozenset[str] = frozenset({"SSH_AUTH_SOCK", "XDG_SESSION_TYPE", "KEYRING_BACKEND", "DBUS_SESSION_BUS_ADDRESS"})


def scrubbed_env(base: Mapping[str, str] | None = None, *, keep: frozenset[str] = frozenset()) -> dict[str, str]:
    """Copy of ``base`` (default ``os.environ``) minus secret-looking variables."""
    src = dict(os.environ if base is None else base)
    out: dict[str, str] = {}
    for name, value in src.items():
        if name in keep or name in _KEEP:
            out[name] = value
            continue
        if name in _ALWAYS_DROP or _SECRET_NAME.search(name):
            continue
        out[name] = value
    return out


__all__ = ["scrubbed_env"]
