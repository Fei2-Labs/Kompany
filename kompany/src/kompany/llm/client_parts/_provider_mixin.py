"""Provider dispatch mixin for LLMClient."""

from __future__ import annotations

import json
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
from kompany.llm.client_parts._types import (
    LLMResponse,
    ToolCallRequest,
    ToolSpec,
)

log = logging.getLogger(__name__)

# Providers whose wire protocol supports NATIVE tool_use (function
# calling). CLI vehicles (claude_code / codex / opencode) shell out
# one-shot and expose no ``tools=`` knob — they are EXCLUDED and keep
# their existing text path. Gemini's OpenAI-compat shim accepts
# ``tools=`` in current SDKs, so it is included with the other
# OpenAI-compatible endpoints; a Custom proxy is trusted to be
# OpenAI-faithful (it already proxies chat.completions).
_NATIVE_TOOL_PROVIDERS: frozenset[Provider] = frozenset({
    Provider.ANTHROPIC,
    Provider.OPENAI,
    Provider.GEMINI,
    Provider.GLM,
    Provider.KIMI,
    Provider.CUSTOM,
    # OAuth-subscription path (06-16-agentic-chat-engine P3): the Codex
    # backend is itself a tool-calling agent runtime, so native tool_use
    # is intact over the OAuth bearer token (research §2).
    Provider.CHATGPT_OAUTH,
})

# Hidden attribution headers the native Codex route expects (research §2:
# "originator, version, User-Agent attached only on that native Codex
# route"). TODO(verify-live): exact header names/values are from OpenClaw
# docs, NOT confirmed against OpenAI; override via
# KOMPANY_CHATGPT_OAUTH_ORIGINATOR / _VERSION if a live login needs it.
_CHATGPT_OAUTH_DEFAULT_HEADERS = {
    "originator": "codex_cli_rs",
    "User-Agent": "kompany-codex-oauth",
}


def provider_supports_native_tools(provider: Provider) -> bool:
    """Capability flag: can this provider do native tool_use?

    The CEO/agentic loop calls this to decide between native tool_use
    and the legacy JSON-injection fallback. CLI providers return False.
    """
    return provider in _NATIVE_TOOL_PROVIDERS


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

    def _get_chatgpt_oauth_client(self) -> Any:
        """Build an OpenAI-compatible client bound to the OAuth bearer token.

        Routes through the Codex backend (research §2). The bearer comes
        from the OAuth token store (auto-refreshed on expiry); the base_url
        + attribution headers target the native Codex route, NOT
        api.openai.com.

        The host LLMClient must expose ``self.oauth_token_store`` (an
        :class:`~kompany.llm.oauth.token_store.OAuthTokenStore`). The engine
        wires it; when absent we raise a clear error rather than silently
        falling back to API billing.
        """
        import os

        import openai

        store = getattr(self, "oauth_token_store", None)
        if store is None:
            raise RuntimeError(
                "chatgpt-oauth provider selected but no OAuth token store is "
                "wired — run `kompany auth openai` and ensure the engine "
                "passes oauth_token_store to LLMClient."
            )
        access_token = store.get_access_token()
        if not access_token:
            raise RuntimeError(
                "not logged in to ChatGPT/Codex — run `kompany auth openai`."
            )
        base_url = (
            os.environ.get("KOMPANY_CHATGPT_OAUTH_BASE_URL")
            or PROVIDER_BASE_URLS.get(Provider.CHATGPT_OAUTH)
        )
        headers = dict(_CHATGPT_OAUTH_DEFAULT_HEADERS)
        originator = os.environ.get("KOMPANY_CHATGPT_OAUTH_ORIGINATOR")
        if originator:
            headers["originator"] = originator
        version = os.environ.get("KOMPANY_CHATGPT_OAUTH_VERSION")
        if version:
            headers["version"] = version
        # A fresh client per access token keeps the bearer current after a
        # refresh (the access token rotates; caching would pin a stale one).
        return openai.OpenAI(
            api_key=access_token,
            base_url=base_url,
            default_headers=headers,
        )

    def _oauth_model_name(self, model: str) -> str:
        """Strip the ``chatgpt-oauth:`` routing prefix to the bare model id.

        The prefix is a Kompany routing marker; the backend wants the plain
        model name (e.g. ``gpt-5``).
        """
        if model.lower().startswith("chatgpt-oauth:"):
            return model.split(":", 1)[1] or model
        return model

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
            # OAuth-subscription routing is an explicit "use my ChatGPT sub"
            # choice — it wins over a configured custom_base_url just like
            # the CLI prefixes do.
            Provider.CHATGPT_OAUTH,
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

    def _openai_client_for(self, provider: Provider, model: str) -> tuple[Any, str]:
        """Return (client, wire_model) for an OpenAI-compatible provider.

        The OAuth-subscription provider needs a bearer-token client + the
        ``chatgpt-oauth:`` prefix stripped from the model id; every other
        OpenAI-compatible provider uses the standard api-key client and the
        model id unchanged.
        """
        if provider == Provider.CHATGPT_OAUTH:
            return self._get_chatgpt_oauth_client(), self._oauth_model_name(model)
        return self._get_openai_client(provider), model

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
        """Call an OpenAI-compatible API (OpenAI, Gemini, GLM, Kimi, custom,
        chatgpt-oauth)."""
        client, model = self._openai_client_for(provider, model)
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

    # ------------------------------------------------------------------
    # native tool_use (06-16-agentic-chat-engine P1)
    # ------------------------------------------------------------------

    def supports_native_tools(self, model: str) -> bool:
        """Whether ``model`` resolves to a native-tool-capable provider."""
        return provider_supports_native_tools(self._resolve_provider(model))

    def _invoke_provider_with_tools(
        self,
        provider: Provider,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int,
        tool_choice: str,
    ) -> LLMResponse:
        """Dispatch a native tool_use call. Per-provider, NOT uniform."""
        if provider == Provider.ANTHROPIC:
            return self._call_anthropic_tools(
                model, system, messages, tools, max_tokens, tool_choice
            )
        if provider in _NATIVE_TOOL_PROVIDERS:
            return self._call_openai_compatible_tools(
                provider, model, system, messages, tools, max_tokens, tool_choice
            )
        raise ValueError(
            f"provider {provider.value!r} does not support native tool_use"
        )

    def _call_anthropic_tools(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int,
        tool_choice: str,
    ) -> LLMResponse:
        """Anthropic Messages API with ``tools=`` + ``tool_use`` blocks.

        ``messages`` is a provider-native multi-turn list (user /
        assistant / tool_result content blocks). The caller threads the
        prior assistant message + ``tool_result`` user message between
        turns; we just translate tools and read ``tool_use`` blocks out.
        """
        client = self._get_anthropic_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "tools": [t.to_anthropic() for t in tools],
        }
        if tool_choice in ("required", "any"):
            kwargs["tool_choice"] = {"type": "any"}
        elif tool_choice == "auto":
            kwargs["tool_choice"] = {"type": "auto"}
        response = client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCallRequest(
                        call_id=block.id,
                        name=block.name,
                        arguments=dict(block.input or {}),
                    )
                )
        # Serialize the assistant turn so the caller can replay it verbatim
        # before appending the tool_result user message (Anthropic requires
        # the assistant tool_use turn to precede its tool_result).
        assistant_msg = {
            "role": "assistant",
            "content": [
                _anthropic_block_to_dict(b) for b in response.content
            ],
        }
        return LLMResponse(
            text="".join(text_parts),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=0.0,
            model=model,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            raw_assistant_message=assistant_msg,
        )

    def _call_openai_compatible_tools(
        self,
        provider: Provider,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int,
        tool_choice: str,
    ) -> LLMResponse:
        """OpenAI-compatible chat.completions with ``tools=``/``tool_calls``.

        ``messages`` already includes the system message at index 0 when
        the caller built the list, but to keep the caller provider-neutral
        we prepend ``system`` here if it isn't already present.
        """
        client, model = self._openai_client_for(provider, model)
        full_messages: list[dict[str, Any]] = []
        if not (messages and messages[0].get("role") == "system"):
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=full_messages,
            tools=[t.to_openai() for t in tools],
            tool_choice=tool_choice,
        )
        choice = response.choices[0].message
        usage = response.usage
        tool_calls: list[ToolCallRequest] = []
        for tc in getattr(choice, "tool_calls", None) or []:
            raw_args = getattr(tc.function, "arguments", "") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (ValueError, TypeError):
                args = {}
            tool_calls.append(
                ToolCallRequest(
                    call_id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                )
            )
        # Replay the assistant message verbatim on the next turn so the
        # tool_call ids line up with the role:"tool" results.
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": choice.content or "",
        }
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.call_id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in tool_calls
            ]
        return LLMResponse(
            text=choice.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cost_usd=0.0,
            model=model,
            tool_calls=tool_calls,
            stop_reason=getattr(response.choices[0], "finish_reason", None),
            raw_assistant_message=assistant_msg,
        )


def _anthropic_block_to_dict(block: Any) -> dict[str, Any]:
    """Convert an Anthropic content block (SDK object) to a plain dict.

    Used to replay the assistant turn back into the next request without
    depending on the SDK's serialization. Falls back to ``model_dump``
    when available.
    """
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "") or ""}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": dict(block.input or {}),
        }
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        return dump()
    return {"type": btype}
