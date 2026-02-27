"""LLM provider definitions and auto-detection."""

from __future__ import annotations

from enum import Enum


class Provider(str, Enum):
    """Supported LLM providers."""

    ANTHROPIC = "anthropic"
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
}

# Prefix-based auto-detection: (prefix, provider)
_MODEL_PREFIX_MAP: list[tuple[str, Provider]] = [
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
