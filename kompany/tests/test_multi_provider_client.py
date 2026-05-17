"""Tests for multi-provider LLM client routing."""

from __future__ import annotations

import pytest

from kompany.llm.client import LLMClient, LLMResponse
from kompany.llm.providers import Provider


class FakeSettings:
    """Minimal settings for routing tests."""

    anthropic_api_key = "sk-ant-test"
    openai_api_key = "sk-openai-test"
    gemini_api_key = "gemini-test"
    glm_api_key = "glm-test"
    kimi_api_key = "kimi-test"
    custom_api_key = ""
    custom_base_url = ""

    def get_api_key_for_provider(self, provider: str) -> str:
        return {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "glm": self.glm_api_key,
            "kimi": self.kimi_api_key,
            "custom": self.custom_api_key,
        }.get(provider, "")


@pytest.fixture
def client():
    return LLMClient(settings=FakeSettings(), cost_tracker=None)


def test_resolve_anthropic(client):
    assert client._resolve_provider("claude-sonnet-4-20250514") == Provider.ANTHROPIC


def test_resolve_openai(client):
    assert client._resolve_provider("gpt-4o") == Provider.OPENAI


def test_resolve_gemini(client):
    assert client._resolve_provider("gemini-2.5-pro") == Provider.GEMINI


def test_resolve_glm(client):
    assert client._resolve_provider("glm-4-plus") == Provider.GLM


def test_resolve_kimi(client):
    assert client._resolve_provider("moonshot-v1-8k") == Provider.KIMI


def test_resolve_unknown_defaults_to_anthropic(client):
    assert client._resolve_provider("some-random-model") == Provider.ANTHROPIC


def test_resolve_unknown_with_custom_endpoint():
    settings = FakeSettings()
    settings.custom_base_url = "https://my-endpoint.example.com/v1"
    settings.custom_api_key = "custom-key"
    c = LLMClient(settings=settings, cost_tracker=None)
    assert c._resolve_provider("my-local-model") == Provider.CUSTOM


def test_lazy_anthropic_client_not_created_on_init(client):
    assert client._anthropic_client is None


def test_lazy_openai_clients_empty_on_init(client):
    assert client._openai_clients == {}


def test_quota_error_invokes_provider_error_handler():
    events = []
    c = LLMClient(
        settings=FakeSettings(),
        cost_tracker=None,
        provider_error_handler=events.append,
    )

    def fail(*args, **kwargs):
        error = RuntimeError("rate limit exceeded")
        error.status_code = 429
        raise error

    c._call_anthropic = fail

    with pytest.raises(RuntimeError, match="rate limit"):
        c.call(
            model="claude-sonnet-4-20250514",
            system="system",
            prompt="prompt",
            agent_name="CEO",
            directive_id="dir-1",
        )

    assert events == [{
        "reason": "quota_exhausted",
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "agent_name": "CEO",
        "directive_id": "dir-1",
        "error_type": "RuntimeError",
        "error": "rate limit exceeded",
    }]


def test_non_quota_error_does_not_invoke_provider_error_handler():
    events = []
    c = LLMClient(
        settings=FakeSettings(),
        cost_tracker=None,
        provider_error_handler=events.append,
    )

    def fail(*args, **kwargs):
        raise RuntimeError("model not found")

    c._call_anthropic = fail

    with pytest.raises(RuntimeError, match="model not found"):
        c.call("claude-sonnet-4-20250514", "system", "prompt")

    assert events == []


def test_successful_call_records_cost_after_provider_response():
    class FakeCostTracker:
        def record(self, **kwargs):
            return 0.25

    c = LLMClient(settings=FakeSettings(), cost_tracker=FakeCostTracker())
    c._call_anthropic = lambda *args, **kwargs: LLMResponse(
        text="ok",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
        model="claude-sonnet-4-20250514",
    )

    response = c.call("claude-sonnet-4-20250514", "system", "prompt")

    assert response.cost_usd == 0.25
