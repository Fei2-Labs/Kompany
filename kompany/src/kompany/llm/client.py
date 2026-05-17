"""Multi-provider LLM client with structured output and cost tracking."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Type, TypeVar

import anthropic
from pydantic import BaseModel

from kompany.llm.cost_tracker import CostTracker
from kompany.llm.models import estimate_cost
from kompany.llm.providers import Provider, PROVIDER_BASE_URLS, detect_provider

T = TypeVar("T", bound=BaseModel)
ProviderErrorHandler = Callable[[dict[str, Any]], None]


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    parsed: Any = None


class LLMClient:
    """Multi-provider LLM client with cost tracking.

    Supports Anthropic (native SDK) and OpenAI-compatible providers
    (OpenAI, Gemini, GLM, Kimi, custom) via the openai SDK.
    """

    def __init__(
        self,
        settings: Any,
        cost_tracker: CostTracker,
        provider_error_handler: ProviderErrorHandler | None = None,
    ):
        self.settings = settings
        self.cost_tracker = cost_tracker
        self.provider_error_handler = provider_error_handler
        self._anthropic_client = None
        self._openai_clients: dict[Provider, Any] = {}

    def _get_anthropic_client(self) -> anthropic.Anthropic:
        """Lazy-init the Anthropic client."""
        if self._anthropic_client is None:
            self._anthropic_client = anthropic.Anthropic(
                api_key=self.settings.anthropic_api_key
            )
        return self._anthropic_client

    def _get_openai_client(self, provider: Provider) -> Any:
        """Lazy-init an OpenAI-compatible client for the given provider."""
        if provider not in self._openai_clients:
            import openai

            api_key = self.settings.get_api_key_for_provider(provider.value)
            if provider == Provider.CUSTOM:
                base_url = self.settings.custom_base_url
            else:
                base_url = PROVIDER_BASE_URLS.get(provider)

            kwargs: dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url

            self._openai_clients[provider] = openai.OpenAI(**kwargs)
        return self._openai_clients[provider]

    def _resolve_provider(self, model: str) -> Provider:
        """Determine which provider to use for a model."""
        detected = detect_provider(model)
        if detected is not None:
            return detected
        # Unknown model: route to custom if configured, else Anthropic
        if self.settings.custom_base_url:
            return Provider.CUSTOM
        return Provider.ANTHROPIC

    def _is_quota_error(self, error: Exception) -> bool:
        status_code = getattr(error, "status_code", None)
        if status_code == 429:
            return True
        text = str(error).lower().replace("_", "-")
        return any(
            marker in text
            for marker in (
                "rate limit",
                "rate-limit",
                "quota",
                "insufficient-quota",
                "too many requests",
                "resource exhausted",
            )
        )

    def _handle_provider_error(
        self,
        error: Exception,
        provider: Provider,
        model: str,
        agent_name: str,
        directive_id: str | None,
    ) -> None:
        if not self.provider_error_handler or not self._is_quota_error(error):
            return
        self.provider_error_handler({
            "reason": "quota_exhausted",
            "provider": provider.value,
            "model": model,
            "agent_name": agent_name,
            "directive_id": directive_id,
            "error_type": type(error).__name__,
            "error": str(error),
        })

    def call(
        self,
        model: str,
        system: str,
        prompt: str,
        agent_name: str = "unknown",
        directive_id: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Make a freeform LLM call, dispatching to the correct provider."""
        provider = self._resolve_provider(model)
        try:
            if provider == Provider.ANTHROPIC:
                resp = self._call_anthropic(model, system, prompt, max_tokens)
            else:
                resp = self._call_openai_compatible(
                    provider, model, system, prompt, max_tokens
                )
        except Exception as exc:
            self._handle_provider_error(
                exc,
                provider=provider,
                model=model,
                agent_name=agent_name,
                directive_id=directive_id,
            )
            raise

        cost = self.cost_tracker.record(
            model=model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            description=f"{agent_name}: {prompt[:60]}",
            directive_id=directive_id,
        )
        resp.cost_usd = cost
        return resp

    def _call_anthropic(
        self, model: str, system: str, prompt: str, max_tokens: int
    ) -> LLMResponse:
        """Call the Anthropic API."""
        client = self._get_anthropic_client()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return LLMResponse(
            text=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=0.0,
            model=model,
        )

    def _call_openai_compatible(
        self,
        provider: Provider,
        model: str,
        system: str,
        prompt: str,
        max_tokens: int,
    ) -> LLMResponse:
        """Call an OpenAI-compatible API (OpenAI, Gemini, GLM, Kimi, custom)."""
        client = self._get_openai_client(provider)
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        choice = response.choices[0].message
        usage = response.usage
        return LLMResponse(
            text=choice.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cost_usd=0.0,
            model=model,
        )

    def call_structured(
        self,
        model: str,
        system: str,
        prompt: str,
        output_schema: Type[T],
        agent_name: str = "unknown",
        directive_id: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Make an LLM call expecting JSON conforming to a Pydantic schema."""
        schema_json = json.dumps(output_schema.model_json_schema(), indent=2)
        full_prompt = (
            f"{prompt}\n\n"
            f"Respond with ONLY valid JSON matching this schema:\n{schema_json}"
        )
        resp = self.call(
            model=model,
            system=system,
            prompt=full_prompt,
            agent_name=agent_name,
            directive_id=directive_id,
            max_tokens=max_tokens,
        )
        # Parse the JSON from the response text
        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = output_schema.model_validate_json(text)
        resp.parsed = parsed
        return resp
