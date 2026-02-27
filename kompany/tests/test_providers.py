"""Tests for provider detection."""

from __future__ import annotations

import pytest

from kompany.llm.providers import Provider, detect_provider


@pytest.mark.parametrize(
    "model, expected",
    [
        ("claude-opus-4-20250514", Provider.ANTHROPIC),
        ("claude-sonnet-4-20250514", Provider.ANTHROPIC),
        ("claude-haiku-4-20250414", Provider.ANTHROPIC),
        ("gpt-4o", Provider.OPENAI),
        ("gpt-4o-mini", Provider.OPENAI),
        ("gpt-4.1", Provider.OPENAI),
        ("o1", Provider.OPENAI),
        ("o3", Provider.OPENAI),
        ("o3-mini", Provider.OPENAI),
        ("o4-mini", Provider.OPENAI),
        ("gemini-2.5-pro", Provider.GEMINI),
        ("gemini-2.0-flash", Provider.GEMINI),
        ("glm-4-plus", Provider.GLM),
        ("glm-4-air", Provider.GLM),
        ("moonshot-v1-8k", Provider.KIMI),
        ("kimi-latest", Provider.KIMI),
    ],
)
def test_detect_provider_known(model, expected):
    assert detect_provider(model) == expected


def test_detect_provider_unknown():
    assert detect_provider("my-custom-model") is None
    assert detect_provider("llama-3-70b") is None


def test_detect_provider_case_insensitive():
    assert detect_provider("Claude-Sonnet-4-20250514") == Provider.ANTHROPIC
    assert detect_provider("GPT-4o") == Provider.OPENAI
    assert detect_provider("Gemini-2.5-pro") == Provider.GEMINI
