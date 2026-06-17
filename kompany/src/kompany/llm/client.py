"""Multi-provider LLM client with structured output and cost tracking."""

from __future__ import annotations

import json
import logging
from typing import Any, Type

import anthropic  # noqa: F401 — kept for callers that do `from kompany.llm.client import anthropic`
from pydantic import BaseModel

from kompany.llm.client_parts._types import (
    DEFAULT_LLM_SILENT_TIMEOUT_SECONDS,
    LLMResponse,
    ProviderErrorHandler,
    T,
    ToolCallRequest,
    ToolSpec,
    _SilentTimeoutMarker,
)
from kompany.llm.client_parts._provider_mixin import ProviderMixin
from kompany.llm.client_parts._watchdog_mixin import WatchdogMixin
from kompany.llm.cost_tracker import CostTracker
from kompany.llm.providers import Provider

log = logging.getLogger(__name__)

# Re-exported at module level so existing ``from kompany.llm.client import X`` works.
__all__ = [
    "LLMClient",
    "LLMResponse",
    "ToolSpec",
    "ToolCallRequest",
    "_SilentTimeoutMarker",
    "ProviderErrorHandler",
    "DEFAULT_LLM_SILENT_TIMEOUT_SECONDS",
]


class LLMClient(ProviderMixin, WatchdogMixin):
    """Multi-provider LLM client with cost tracking.

    Supports Anthropic (native SDK) and OpenAI-compatible providers
    (OpenAI, Gemini, GLM, Kimi, custom) via the openai SDK.
    """

    def __init__(
        self,
        settings: Any,
        cost_tracker: CostTracker,
        provider_error_handler: ProviderErrorHandler | None = None,
        audit_log: Any = None,
        watchdog: Any = None,
        silent_timeout_seconds: float | None = None,
        fallback_models: list[str] | None = None,
        oauth_token_store: Any = None,
    ):
        self.settings = settings
        # OAuth-subscription token sink (06-16-agentic-chat-engine P3).
        # When wired (engine passes an
        # :class:`~kompany.llm.oauth.token_store.OAuthTokenStore`), the
        # ``chatgpt-oauth:*`` provider path authenticates with the stored
        # bearer token (auto-refreshed). None = no OAuth login configured;
        # selecting that provider then raises a clear "run kompany auth
        # openai" error rather than silently misrouting to API billing.
        self.oauth_token_store = oauth_token_store
        self.cost_tracker = cost_tracker
        self.provider_error_handler = provider_error_handler
        # Optional audit log: when set, every successful LLM call writes an
        # ``llm.call`` event carrying the active run_id. The engine wires
        # this on construction; standalone tests can leave it None.
        self.audit_log = audit_log
        # Optional watchdog: when set, ``call()`` enforces a silent-run
        # timeout and performs a single retry. When None (standalone test
        # use), the client falls back to the unguarded direct call. The
        # engine always wires one in production.
        self.watchdog = watchdog
        self.silent_timeout_seconds = (
            float(silent_timeout_seconds)
            if silent_timeout_seconds is not None
            else float(DEFAULT_LLM_SILENT_TIMEOUT_SECONDS)
        )
        # Model-fallback pool (ADR-0005 lane-worker contract): when the
        # primary model is exhausted after the watchdog's own retry, the
        # call retries once per fallback model before raising
        # ``LLMUnavailable``. None/empty = no fallback (legacy behaviour).
        self.fallback_models = list(fallback_models or [])
        self._anthropic_client = None
        self._openai_clients: dict[Provider, Any] = {}

    def call(
        self,
        model: str,
        system: str,
        prompt: str,
        agent_name: str = "unknown",
        directive_id: str | None = None,
        max_tokens: int = 4096,
        task_id: str | None = None,
        project_id: str | None = None,
        action_type: str | None = None,
        provider_override: Provider | None = None,
    ) -> LLMResponse:
        """Make a freeform LLM call, dispatching to the correct provider.

        Resilience semantics (see ``05-18-resilience-foundation``):

        * If a :class:`~kompany.core.watchdog.Watchdog` is wired and the
          provider hasn't returned within ``silent_timeout_seconds``, a
          ``silent_run`` health event is written. The in-flight call is
          **not** cancelled; it continues to run on its worker thread.
        * If the call eventually succeeds, a ``recovered`` event is
          written and the matching ``silent_run`` is closed.
        * If the call raises (network / 429 / 5xx / etc.), a single retry
          is attempted. Both attempts write a ledger entry (no "free"
          retries).
        * If the retry also fails, a ``retry_exhausted`` event is written
          and :class:`LLMUnavailable` is raised so the engine can
          transition the owning task to ``stranded_in_progress``.

        When ``watchdog`` is None (standalone test use), behaviour is the
        legacy single-shot call with no timeout / retry.

        ``action_type`` is the call-site label used in the SSE
        ``llm.spend`` payload (see ``05-19-cost-visibility-discipline``).
        Optional; when ``None`` the spend event falls back to
        ``"other"``.
        """
        provider = provider_override or self._resolve_provider(model)
        if self.watchdog is None:
            # Legacy unguarded path for callers (or tests) that haven't
            # wired a watchdog. Preserves prior behaviour exactly.
            try:
                resp = self._invoke_provider(
                    provider, model, system, prompt, max_tokens,
                    agent_name=agent_name, directive_id=directive_id,
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
            return self._record_success(
                resp,
                provider=provider,
                model=model,
                prompt=prompt,
                agent_name=agent_name,
                directive_id=directive_id,
                action_type=action_type,
            )

        # Resilient path: timeout + single retry.
        return self._call_with_watchdog(
            provider=provider,
            model=model,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
            agent_name=agent_name,
            directive_id=directive_id,
            task_id=task_id,
            project_id=project_id,
            action_type=action_type,
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
        action_type: str | None = None,
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
            action_type=action_type,
        )
        # Parse the JSON from the response text
        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = output_schema.model_validate_json(text)
        resp.parsed = parsed
        return resp

    def call_with_tools(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        agent_name: str = "unknown",
        directive_id: str | None = None,
        max_tokens: int = 4096,
        action_type: str | None = None,
        tool_choice: str = "auto",
        provider_override: Provider | None = None,
    ) -> LLMResponse:
        """One native tool_use turn (06-16-agentic-chat-engine P1).

        Uses PROVIDER-NATIVE function calling — Anthropic ``tools=`` +
        ``tool_use`` blocks, OpenAI/OpenAI-compatible ``tools=`` +
        ``tool_calls``. ``messages`` is a provider-native multi-turn list
        (the caller threads the prior assistant turn + tool results
        between calls). The returned :class:`LLMResponse` carries
        ``tool_calls`` (normalized) and ``raw_assistant_message`` (replay
        verbatim on the next turn).

        Cost/usage is booked through the same path as ``call`` /
        ``call_structured`` (``cost_tracker.record`` + ``llm.call``
        audit) via :meth:`_record_success`, keyed by ``action_type``.

        Raises ``ValueError`` for providers that can't do native tool_use
        (CLI vehicles) — callers must check :meth:`supports_native_tools`
        and fall back to the JSON protocol.
        """
        provider = provider_override or self._resolve_provider(model)
        try:
            resp = self._invoke_provider_with_tools(
                provider=provider,
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                tool_choice=tool_choice,
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
        # Book cost/usage exactly like the other success exits. The
        # "prompt" label uses the latest user content for the ledger
        # description, falling back to the system prompt.
        last_user = next(
            (m for m in reversed(messages) if m.get("role") == "user"),
            None,
        )
        desc = str((last_user or {}).get("content", system))[:60]
        return self._record_success(
            resp,
            provider=provider,
            model=model,
            prompt=desc,
            agent_name=agent_name,
            directive_id=directive_id,
            action_type=action_type,
        )
