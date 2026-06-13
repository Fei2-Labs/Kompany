"""Provider dispatch mixin for LLMClient."""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from kompany.llm.claude_code import (
    DEFAULT_TIMEOUT_SECONDS as CLAUDE_CODE_DEFAULT_TIMEOUT,
    run_claude_code,
)
from kompany.llm.cli_providers import (
    DEFAULT_TIMEOUT_SECONDS as CLI_DEFAULT_TIMEOUT,
    run_cli_completion,
)
from kompany.llm.providers import Provider, PROVIDER_BASE_URLS, detect_provider
from kompany.llm.client_parts._types import LLMResponse

log = logging.getLogger(__name__)


class ProviderMixin:
    """Mixin providing provider dispatch methods for LLMClient.

    Assumes the host class provides:
      - self.settings
      - self.silent_timeout_seconds
      - self._anthropic_client
      - self._openai_clients (dict[Provider, Any])
      - self.provider_error_handler
    """

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
        """Determine which provider to use for a model.

        Resolution order:
          1. CLI-provider model ids (``claude-code:*``, ``codex:*``,
             ``opencode:*``) always route to the corresponding local
             CLI. The prefix is an explicit operator choice ("use my
             subscription CLI, not an API endpoint"), so it wins even
             over a configured custom base_url — a custom
             OpenAI-compatible proxy can't serve a CLI-only id anyway.
          2. If the operator wired a custom OpenAI-compatible endpoint
             (``custom_base_url`` set), route through it regardless of
             the model name AND regardless of whether custom_api_key
             is currently loaded on the in-memory settings. Custom
             endpoints commonly proxy upstream ids (gpt-5.5,
             claude-sonnet-4, gemini-2-flash, ...); the operator's
             intent when they provided a base_url is "send everything
             through THIS endpoint." If the api_key is missing the
             OpenAI SDK will surface a clear 401 against the right
             endpoint — that's a fixable user-side issue, not a reason
             to silently misroute to api.openai.com.
          3. Otherwise, infer from the model name prefix.
          4. Final fallback: Anthropic.
        """
        detected = detect_provider(model)
        if detected in (
            Provider.CLAUDE_CODE,
            Provider.CODEX_CLI,
            Provider.OPENCODE_CLI,
        ):
            return detected
        if self.settings.custom_base_url:
            return Provider.CUSTOM
        if detected is not None:
            return detected
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

    def _invoke_provider(
        self,
        provider: Provider,
        model: str,
        system: str,
        prompt: str,
        max_tokens: int,
        *,
        agent_name: str,
        directive_id: str | None,
    ) -> LLMResponse:
        """Dispatch to the right provider and return the raw response.

        The provider-error handler is **not** invoked here — callers
        decide when to call it (only after a real failure, not after a
        soft timeout).
        """
        if provider == Provider.CLAUDE_CODE:
            return self._call_claude_code(model, system, prompt, max_tokens)
        if provider in (Provider.CODEX_CLI, Provider.OPENCODE_CLI):
            return self._call_cli_provider(provider, model, system, prompt)
        if provider == Provider.ANTHROPIC:
            return self._call_anthropic(model, system, prompt, max_tokens)
        return self._call_openai_compatible(
            provider, model, system, prompt, max_tokens
        )

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

    def _call_claude_code(
        self, model: str, system: str, prompt: str, max_tokens: int
    ) -> LLMResponse:
        """Call the locally installed ``claude`` CLI in headless mode.

        Subprocess mechanics live in :mod:`kompany.llm.claude_code`.
        ``max_tokens`` is accepted for signature parity with the other
        providers but not forwarded — the CLI's print mode has no
        max-tokens knob.
        """
        timeout = self.silent_timeout_seconds or CLAUDE_CODE_DEFAULT_TIMEOUT
        text, input_tokens, output_tokens = run_claude_code(
            model, system, prompt, timeout
        )
        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0,
            model=model,
        )

    def _call_cli_provider(
        self, provider: Provider, model: str, system: str, prompt: str
    ) -> LLMResponse:
        """Call a locally installed agent CLI (codex / opencode) one-shot.

        Subprocess mechanics live in :mod:`kompany.llm.cli_providers`.
        Like ``_call_claude_code`` there's no max-tokens knob — the CLIs
        don't expose one in single-shot mode.
        """
        cli = "codex" if provider == Provider.CODEX_CLI else "opencode"
        timeout = self.silent_timeout_seconds or CLI_DEFAULT_TIMEOUT
        text, input_tokens, output_tokens = run_cli_completion(
            cli, model, system, prompt, timeout
        )
        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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
