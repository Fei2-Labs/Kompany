"""LLM provider definitions and auto-detection."""

from __future__ import annotations

from enum import Enum


class Provider(str, Enum):
    """Supported LLM providers."""

    ANTHROPIC = "anthropic"
    CLAUDE_CODE = "claude_code"
    # Local agent CLIs as single-shot providers (issue #18). Like
    # CLAUDE_CODE they reuse the CLI's saved subscription auth — no API
    # key. Model id convention: "<cli>:<model>" (codex:gpt-5,
    # opencode:openai/gpt-5).
    CODEX_CLI = "codex_cli"
    OPENCODE_CLI = "opencode_cli"
    # OAuth-subscription provider (06-16-agentic-chat-engine P3). Routes
    # native tool_use calls through the Codex/ChatGPT backend
    # (chatgpt.com/backend-api) authenticated by a stored OAuth bearer
    # token — NOT an API key, NOT a CLI shell-out. Model id convention:
    # "chatgpt-oauth:<model>" (e.g. chatgpt-oauth:gpt-5).
    CHATGPT_OAUTH = "chatgpt_oauth"
    OPENAI = "openai"
    GEMINI = "gemini"
    GLM = "glm"
    KIMI = "kimi"
    CUSTOM = "custom"


# Base URLs for OpenAI-compatible providers.
# OpenAI itself uses the SDK default, so it's not listed here.
PROVIDER_BASE_URLS: dict[Provider, str] = {
    Provider.GEMINI: "https://generativelanguage.googleapis.com/v1beta/openai/",
    Provider.GLM: "https://open.bigmodel.cn/api/paas/v4/",
    Provider.KIMI: "https://api.moonshot.cn/v1",
    # OAuth-subscription path (06-16-agentic-chat-engine P3). The Codex
    # backend the Codex CLI itself uses, NOT api.openai.com — spending
    # ChatGPT subscription quota rather than API billing (research §2).
    # TODO(verify-live): research/subscription-oauth-and-openclaw.md §2
    # cites `chatgpt.com/backend-api` from OpenClaw docs, NOT confirmed
    # against OpenAI. The OpenAI-compatible chat-completions sub-path is a
    # best guess; verify the real path + body shape against a live Codex
    # login. Overridable via KOMPANY_CHATGPT_OAUTH_BASE_URL.
    Provider.CHATGPT_OAUTH: "https://chatgpt.com/backend-api/codex",
}

# Prefix-based auto-detection: (prefix, provider). Order matters — first
# match wins, so "claude-code" must precede the broader "claude-" prefix.
_MODEL_PREFIX_MAP: list[tuple[str, Provider]] = [
    # CLI providers first: explicit "<cli>:" prefixes are operator
    # choices and must win over any broader provider prefix.
    ("claude-code", Provider.CLAUDE_CODE),
    # OAuth-subscription prefix must precede the broader "codex:" CLI
    # prefix: "chatgpt-oauth:" is an explicit "use my ChatGPT sub via the
    # native loop" choice, distinct from the codex CLI shell-out.
    ("chatgpt-oauth:", Provider.CHATGPT_OAUTH),
    ("codex:", Provider.CODEX_CLI),
    ("opencode:", Provider.OPENCODE_CLI),
    ("claude-", Provider.ANTHROPIC),
    ("gpt-", Provider.OPENAI),
    ("o1", Provider.OPENAI),
    ("o3", Provider.OPENAI),
    ("o4", Provider.OPENAI),
    ("gemini-", Provider.GEMINI),
    ("glm-", Provider.GLM),
    ("moonshot-", Provider.KIMI),
    ("kimi-", Provider.KIMI),
]


def detect_provider(model: str) -> Provider | None:
    """Detect the provider for a model name based on prefix matching.

    Returns None if the model doesn't match any known prefix.
    """
    model_lower = model.lower()
    for prefix, provider in _MODEL_PREFIX_MAP:
        if model_lower.startswith(prefix):
            return provider
    return None


def list_openai_compatible_models(base_url: str, api_key: str) -> list[str]:
    """Fetch model ids from any OpenAI-compatible ``/models`` endpoint.

    Used by the onboarding flow to populate the custom-provider model
    picker. This call reads metadata only — no LLM completion is
    generated — so it does NOT route through CostTracker. It lives here
    (rather than in the installer) so the test_llm_spend_coverage scan
    sees one well-known SDK touchpoint instead of an ad-hoc client
    instantiation outside the llm/ layer.
    """
    import openai

    # Hard timeout: without one, a slow/hung provider /models endpoint
    # blocks the FastAPI threadpool worker indefinitely and the whole
    # settings page hangs on this single call. 5s is plenty for a
    # metadata list; on timeout the settings UI shows the error inline
    # instead of spinning forever.
    client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=5.0)
    page = client.models.list()
    return [m.id for m in getattr(page, "data", []) if getattr(m, "id", None)]
