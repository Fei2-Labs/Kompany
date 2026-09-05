"""Environment for CLI-provider subprocesses (``claude``, ``opencode``, …).

Two problems, one builder (#45, #30):

* **Secret spill.** The engine's environment carries every provider key and
  the vault key. A CLI provider needs *its own* auth and nothing else, so
  start from :func:`~kompany.core.harness.env_scrub.scrubbed_env` and add
  back only the variables that CLI reads for authentication.
* **Nested-harness coupling.** When Kompany itself runs under Claude Code
  (kompany-mcp inside a session), the child ``claude`` inherits
  ``CLAUDECODE`` / ``CLAUDE_CODE_SESSION_ID`` / ``CLAUDE_CODE_ENTRYPOINT`` /
  ``AI_AGENT`` and behaves as a nested session — the path behind the 300 s
  stalls in #30. Those markers are always stripped.
"""

from __future__ import annotations

import os
from typing import Iterable

from kompany.core.harness.env_scrub import scrubbed_env

# Auth the CLIs read themselves. Everything else secret-looking stays out.
CLI_AUTH_PASSTHROUGH: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"),
    "opencode": ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT"),
    "codex": ("OPENAI_API_KEY", "CODEX_HOME"),
}

# Markers that make a child CLI think it is a nested Claude Code session.
NESTED_HARNESS_MARKERS: tuple[str, ...] = (
    "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT",
    "AI_AGENT", "MCP_SERVER_NAME",
)


def cli_child_env(cli: str, *, extra_keep: Iterable[str] = ()) -> dict[str, str]:
    """Minimal, harness-free environment for one CLI provider child."""
    keep = frozenset(CLI_AUTH_PASSTHROUGH.get(cli, ())) | frozenset(extra_keep)
    env = scrubbed_env(keep=keep)
    for marker in NESTED_HARNESS_MARKERS:
        env.pop(marker, None)
    # Make sure PATH survives even under an unusual parent environment.
    env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    return env


__all__ = ["CLI_AUTH_PASSTHROUGH", "NESTED_HARNESS_MARKERS", "cli_child_env"]
