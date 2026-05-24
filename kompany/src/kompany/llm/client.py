"""Multi-provider LLM client with structured output and cost tracking."""

from __future__ import annotations

import concurrent.futures
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Type, TypeVar

import anthropic
from pydantic import BaseModel

from kompany.core.run_context import current_run_id
from kompany.core.watchdog import LLMUnavailable
from kompany.llm.cost_tracker import CostTracker
from kompany.llm.models import estimate_cost
from kompany.llm.providers import Provider, PROVIDER_BASE_URLS, detect_provider

log = logging.getLogger(__name__)

# Default silent-run timeout. Engine overrides via the ``llm_silent_timeout_seconds``
# field from ``company_config``. Kept module-level so unit tests can monkeypatch.
DEFAULT_LLM_SILENT_TIMEOUT_SECONDS = 90

T = TypeVar("T", bound=BaseModel)
ProviderErrorHandler = Callable[[dict[str, Any]], None]


class _SilentTimeoutMarker(BaseException):
    """Sentinel raised inside the LLM client when a soft timeout fires.

    Subclasses ``BaseException`` (not ``Exception``) so the broad
    ``except Exception`` blocks in :meth:`LLMClient.call` won't swallow
    it; the wrapper logic handles it explicitly. Not part of the public
    API — never raised outside ``client.py``.
    """

    def __init__(
        self,
        future: "concurrent.futures.Future[LLMResponse]",
        event_id: str | None,
    ):
        super().__init__("silent_timeout")
        self.future = future
        self.event_id = event_id


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    parsed: Any = None
    # Set to True by :class:`CostTracker.record` (and by
    # ``record_ai_cost``) after the response has been booked against the
    # ledger. Callers can read this to avoid double-recording the same
    # response — see ``llm/cost_ledger.py``. Not part of the wire format.
    _cost_recorded: bool = False


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
        audit_log: Any = None,
        watchdog: Any = None,
        silent_timeout_seconds: float | None = None,
    ):
        self.settings = settings
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
        """Determine which provider to use for a model.

        Resolution order:
          1. If the operator wired a custom OpenAI-compatible endpoint
             (both ``custom_base_url`` AND ``custom_api_key`` set), route
             through it regardless of the model name. Custom endpoints
             commonly proxy upstream ids (gpt-5.5, claude-sonnet-4,
             gemini-2-flash, ...); the operator's intent when they
             provided a base_url is "send everything through THIS
             endpoint."
          2. Otherwise, infer from the model name prefix.
          3. Final fallback: custom (if only base_url is set) or
             Anthropic.
        """
        if self.settings.custom_base_url and self.settings.custom_api_key:
            return Provider.CUSTOM
        detected = detect_provider(model)
        if detected is not None:
            return detected
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

    def _call_with_watchdog(
        self,
        *,
        provider: Provider,
        model: str,
        system: str,
        prompt: str,
        max_tokens: int,
        agent_name: str,
        directive_id: str | None,
        task_id: str | None,
        project_id: str | None,
        action_type: str | None = None,
    ) -> LLMResponse:
        """Inner: run the call with timeout-based silent_run detection + 1 retry."""
        silent_seconds = self.silent_timeout_seconds
        # Capture context vars from the *calling* thread so the worker
        # thread sees the same run_id when it records the ledger entry.
        import contextvars
        ctx = contextvars.copy_context()

        def _provider_call() -> LLMResponse:
            return ctx.run(
                self._invoke_provider,
                provider,
                model,
                system,
                prompt,
                max_tokens,
                agent_name=agent_name,
                directive_id=directive_id,
            )

        silent_event_id: str | None = None
        # First attempt --------------------------------------------------
        try:
            resp = self._await_with_silent_timeout(
                _provider_call,
                silent_seconds=silent_seconds,
                on_timeout=lambda: self._on_silent_timeout(
                    task_id=task_id,
                    project_id=project_id,
                    detail={
                        "agent_name": agent_name,
                        "model": model,
                        "attempt": 1,
                    },
                ),
            )
        except _SilentTimeoutMarker as marker:
            silent_event_id = marker.event_id
            # Wait the rest out — call may still succeed.
            try:
                resp = marker.future.result()
                # It came back. Record success and emit recovered.
                final = self._record_success(
                    resp,
                    provider=provider,
                    model=model,
                    prompt=prompt,
                    agent_name=agent_name,
                    directive_id=directive_id,
                    action_type=action_type,
                )
                self._emit_recovered(
                    task_id=task_id,
                    project_id=project_id,
                    detail={
                        "agent_name": agent_name,
                        "model": model,
                        "silent_event_id": silent_event_id,
                        "outcome": "slow_success",
                    },
                )
                return final
            except Exception as exc:
                # Slow failure: fall through to retry logic.
                first_error = exc
            else:
                first_error = None  # pragma: no cover — defensive
        except Exception as exc:
            # Fast failure (timeout never tripped).
            first_error = exc
        else:
            # Fast success on first try — done.
            return resp

        # Provider error handler runs only on real exceptions.
        self._handle_provider_error(
            first_error,
            provider=provider,
            model=model,
            agent_name=agent_name,
            directive_id=directive_id,
        )

        # If we didn't already emit a silent_run, this looks like a fast
        # failure — still worth a silent_run row so the operator knows a
        # provider hiccup occurred. (Paperclip-consistent: every retry is
        # preceded by an observable warning.)
        if silent_event_id is None and self.watchdog is not None:
            evt = self.watchdog.record_silent_run(
                task_id=task_id,
                project_id=project_id,
                detail={
                    "agent_name": agent_name,
                    "model": model,
                    "attempt": 1,
                    "error_type": type(first_error).__name__,
                    "error": str(first_error)[:500],
                    "kind": "fast_failure",
                },
            )
            silent_event_id = evt.get("id")

        # Second attempt -------------------------------------------------
        try:
            resp = self._await_with_silent_timeout(
                _provider_call,
                silent_seconds=silent_seconds,
                on_timeout=lambda: self._on_silent_timeout(
                    task_id=task_id,
                    project_id=project_id,
                    detail={
                        "agent_name": agent_name,
                        "model": model,
                        "attempt": 2,
                    },
                ),
            )
        except _SilentTimeoutMarker as marker:
            try:
                resp = marker.future.result()
                final = self._record_success(
                    resp,
                    provider=provider,
                    model=model,
                    prompt=prompt,
                    agent_name=agent_name,
                    directive_id=directive_id,
                    action_type=action_type,
                )
                self._emit_recovered(
                    task_id=task_id,
                    project_id=project_id,
                    detail={
                        "agent_name": agent_name,
                        "model": model,
                        "silent_event_id": marker.event_id,
                        "outcome": "slow_success_retry",
                    },
                )
                return final
            except Exception as exc:
                second_error = exc
        except Exception as exc:
            second_error = exc
        else:
            # Success on retry — record + emit recovered.
            final = self._record_success(
                resp,
                provider=provider,
                model=model,
                prompt=prompt,
                agent_name=agent_name,
                directive_id=directive_id,
                action_type=action_type,
            )
            self._emit_recovered(
                task_id=task_id,
                project_id=project_id,
                detail={
                    "agent_name": agent_name,
                    "model": model,
                    "silent_event_id": silent_event_id,
                    "outcome": "retry_success",
                },
            )
            return final

        # Both attempts failed --------------------------------------------
        self._handle_provider_error(
            second_error,
            provider=provider,
            model=model,
            agent_name=agent_name,
            directive_id=directive_id,
        )
        if self.watchdog is not None:
            self.watchdog.record_retry_exhausted(
                task_id=task_id,
                project_id=project_id,
                detail={
                    "agent_name": agent_name,
                    "model": model,
                    "first_error_type": type(first_error).__name__,
                    "first_error": str(first_error)[:500],
                    "second_error_type": type(second_error).__name__,
                    "second_error": str(second_error)[:500],
                },
            )
        raise LLMUnavailable(
            f"LLM '{model}' unavailable after retry: "
            f"{type(second_error).__name__}: {second_error}"
        ) from second_error

    # ------------------------------------------------------------------
    # Watchdog plumbing helpers
    # ------------------------------------------------------------------

    def _await_with_silent_timeout(
        self,
        provider_call: Callable[[], "LLMResponse"],
        silent_seconds: float,
        on_timeout: Callable[[], str | None],
    ) -> "LLMResponse":
        """Run ``provider_call`` in a worker thread with a soft timeout.

        Behaviour:

        * If the call completes within ``silent_seconds``, the result is
          returned.
        * If the deadline passes first, ``on_timeout`` is called (writes
          a ``silent_run`` row and returns the event id) and a
          :class:`_SilentTimeoutMarker` is raised carrying the still-pending
          ``Future`` so the caller can decide whether to wait it out.
        * If the call raises before the deadline, the exception is
          re-raised verbatim.
        """
        # Single-worker executor so we don't accidentally hold a global
        # thread pool open. Created per-call: cheap, and avoids leaking
        # threads on engine shutdown.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(provider_call)
        try:
            try:
                return future.result(timeout=silent_seconds)
            except concurrent.futures.TimeoutError:
                # Soft timeout — record silent_run, surface marker so the
                # caller can await the slow call's eventual outcome.
                evt_id = on_timeout()
                raise _SilentTimeoutMarker(future=future, event_id=evt_id)
        finally:
            # Tell the executor not to accept new work. Workers in flight
            # keep running; the future is what the caller relies on.
            executor.shutdown(wait=False)

    def _on_silent_timeout(
        self,
        task_id: str | None,
        project_id: str | None,
        detail: dict[str, Any],
    ) -> str | None:
        """Record a ``silent_run`` health event. Returns its id (or None)."""
        if self.watchdog is None:
            return None
        try:
            evt = self.watchdog.record_silent_run(
                task_id=task_id,
                project_id=project_id,
                detail=detail,
            )
            return evt.get("id")
        except Exception:  # noqa: BLE001
            log.debug("watchdog.record_silent_run failed", exc_info=True)
            return None

    def _emit_recovered(
        self,
        task_id: str | None,
        project_id: str | None,
        detail: dict[str, Any],
    ) -> None:
        if self.watchdog is None:
            return
        try:
            self.watchdog.record_recovered(
                task_id=task_id,
                project_id=project_id,
                detail=detail,
            )
        except Exception:  # noqa: BLE001
            log.debug("watchdog.record_recovered failed", exc_info=True)

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
        if provider == Provider.ANTHROPIC:
            return self._call_anthropic(model, system, prompt, max_tokens)
        return self._call_openai_compatible(
            provider, model, system, prompt, max_tokens
        )

    def _record_success(
        self,
        resp: LLMResponse,
        provider: Provider,
        model: str,
        prompt: str,
        agent_name: str,
        directive_id: str | None,
        action_type: str | None = None,
    ) -> LLMResponse:
        """Record cost + audit for a successful call. Returns ``resp``.

        ``action_type`` is forwarded to :meth:`CostTracker.record` so the
        SSE ``llm.spend`` envelope carries a call-site label (see
        ``05-19-cost-visibility-discipline``). When ``None`` the tracker
        falls back to ``"other"``.
        """
        rid = current_run_id()
        cost = self.cost_tracker.record(
            model=model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            description=f"{agent_name}: {prompt[:60]}",
            directive_id=directive_id,
            run_id=rid,
            action_type=action_type,
        )
        resp.cost_usd = cost
        # Mark so explicit ``record_ai_cost`` calls don't double-book.
        try:
            resp._cost_recorded = True
        except Exception:  # pragma: no cover — defensive
            pass
        if self.audit_log is not None:
            try:
                self.audit_log.record(
                    "llm.call",
                    f"LLM call: {agent_name} -> {model}",
                    detail={
                        "model": model,
                        "provider": provider.value,
                        "agent_name": agent_name,
                        "input_tokens": resp.input_tokens,
                        "output_tokens": resp.output_tokens,
                        "cost_usd": cost,
                    },
                    agent_role=agent_name,
                    directive_id=directive_id,
                    run_id=rid,
                )
            except Exception:
                # Audit failure must never break the LLM call path.
                pass
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
