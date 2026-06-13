"""Watchdog / resilience mixin for LLMClient."""

from __future__ import annotations

import concurrent.futures
import logging
from collections.abc import Callable
from typing import Any

from kompany.core.run_context import current_run_id
from kompany.core.watchdog import LLMUnavailable
from kompany.llm.client_parts._types import LLMResponse, _SilentTimeoutMarker
from kompany.llm.providers import Provider

log = logging.getLogger(__name__)


class WatchdogMixin:
    """Mixin providing watchdog / resilience methods for LLMClient.

    Assumes the host class provides:
      - self.watchdog
      - self.cost_tracker
      - self.audit_log
      - self.silent_timeout_seconds
      - self.provider_error_handler
      - self._invoke_provider(...)
      - self._handle_provider_error(...)
    """

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
            agent_name=agent_name,
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
            # Fast success on first try — record cost/audit like every
            # other success exit (slow-success and retry-success already
            # do). Without this, the common path books $0 forever: no
            # ledger entry, no ``llm.call`` audit, no ``llm.spend`` SSE.
            return self._record_success(
                resp,
                provider=provider,
                model=model,
                prompt=prompt,
                agent_name=agent_name,
                directive_id=directive_id,
                action_type=action_type,
            )

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
