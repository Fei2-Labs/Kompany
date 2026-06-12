"""Onboarding LLM connectivity ping.

Split out of api.py per ADR-0003 (06-12-adr3-splits). Handler bodies are
verbatim moves onto a domain ``APIRouter``; route paths are unchanged.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import hmac  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import time  # noqa: F401
from pathlib import Path  # noqa: F401
from secrets import compare_digest  # noqa: F401
from typing import Any, AsyncIterator  # noqa: F401

from fastapi import (  # noqa: F401
    APIRouter,
    BackgroundTasks,
    Body,
    Form,
    Header,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse  # noqa: F401
from pydantic import BaseModel, ConfigDict, Field  # noqa: F401

from kompany.core.event_hub import get_event_hub  # noqa: F401
from kompany.interfaces.api_parts.deps import get_engine, reset_engine  # noqa: F401
from kompany.interfaces.api_parts.onboarding import (  # noqa: F401
    PingPricing,
    PingRequest,
    PingResponse,
)

router = APIRouter()


def _classify_ping_error(detail: str) -> str:
    """Map the ``_ping_llm`` failure detail string to an error_code enum.

    ``_ping_llm`` returns ``"{ExceptionType}: {message}"`` on failure. We
    sniff for HTTP status hints + provider-SDK exception type names to
    bucket the failure into the five categories the frontend renders.
    """
    lowered = detail.lower()
    # Network errors first — connection refused / timeouts come from
    # ``httpx``/``openai``/``anthropic`` SDK error types that all carry
    # "connection" or "timeout" in their class name or message.
    if any(
        marker in lowered
        for marker in (
            "connectionerror",
            "apiconnectionerror",
            "connecterror",
            "connect_error",
            "connection refused",
            "connection error",
            "timeout",
            "timed out",
            "name or service not known",
            "dns",
            "network is unreachable",
        )
    ):
        return "network"
    # Auth / invalid key.
    if any(
        marker in lowered
        for marker in (
            "authenticationerror",
            "permissionerror",
            "permissiondeniederror",
            "invalid_api_key",
            "invalid api key",
            "invalid x-api-key",
            "401",
            "unauthorized",
            "forbidden",
            "403",
        )
    ):
        return "unauthorized"
    # Quota / rate-limit.
    if any(
        marker in lowered
        for marker in (
            "ratelimiterror",
            "rate limit",
            "rate-limit",
            "rate_limit",
            "429",
            "too many requests",
            "quota",
            "resource exhausted",
        )
    ):
        return "rate_limited"
    # Provider 5xx / internal server error / bad gateway.
    if any(
        marker in lowered
        for marker in (
            "internalservererror",
            "serviceunavailable",
            "badgateway",
            "500",
            "502",
            "503",
            "504",
            "internal server error",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            # The provider accepted auth but rejected the request itself —
            # e.g. a custom/OpenAI-compatible endpoint that doesn't support
            # a param or model (swedeapi gpt-5.5 returned "Param Incorrect /
            # invalid_request_error / upstream_error"). Bucket as
            # provider_error so the UI says "try a different model/params".
            "badrequesterror",
            "bad request",
            "invalid_request_error",
            "invalid request",
            "param incorrect",
            "upstream_error",
            "upstream error",
            "400",
        )
    ):
        return "provider_error"
    return "unknown"


@router.post("/onboarding/ping", response_model=PingResponse)
def onboarding_ping(req: PingRequest) -> PingResponse:
    """Standalone connectivity probe wrapping ``installer._ping_llm``.

    The in-window onboarding wizard calls this from the Connection step
    **before** the founder submits the full form, so a bad API key (or
    an unreachable provider) is caught at fail-fast time instead of
    cascading through template apply + first-directive dispatch.

    The handler is intentionally **stateless**:

    * It does not touch the DB, the credential vault, the audit log,
      the episode store, or the cost ledger.
    * It does not call ``record_ai_cost``. The ping prompt is 10 input
      tokens and capped at ~50 output tokens, so the founder's wallet
      sees no measurable charge from a ping.

    This is the **only** sanctioned exception to the engineering
    cost-visibility discipline ("every LLM call must record a ledger
    row"). The exception is justified because the ping is a transient
    health check whose outcome is shown to the founder synchronously
    — there is no decision downstream that depends on its cost being
    in the ledger. The underlying LLM provider still bills the call
    on their side; that's their problem to surface, not ours.

    Errors are classified into the five-value ``error_code`` enum:
    ``unauthorized | rate_limited | network | provider_error | unknown``.
    See :func:`_classify_ping_error`.
    """
    import logging

    from kompany.config.settings import KompanySettings
    from kompany.installer.onboard import (
        PROVIDER_VAULT_KEYS,
        _list_custom_models,
        _pick_latest_custom_model,
        _ping_claude_code,
        _ping_llm,
        _ping_model_for_provider,
    )
    from kompany.llm.models import PRICING

    log = logging.getLogger("kompany.onboarding.ping")

    def _settings_factory() -> KompanySettings:
        # Build a transient settings shim so ``_ping_llm`` can read the
        # API key (and optional base_url) off the in-memory instance
        # without touching the on-disk vault.
        settings = KompanySettings()
        attr = PROVIDER_VAULT_KEYS.get(req.provider)
        if attr:
            setattr(settings, attr, req.api_key)
        if req.base_url:
            # ``custom_base_url`` is the only base_url-shaped knob the
            # client understands today; route through it regardless of
            # provider so a custom endpoint override works for any one.
            setattr(settings, "custom_base_url", req.base_url)
        return settings

    # claude_code provider: no SDK, no API key. Probe the local `claude`
    # CLI via installer.onboard._ping_claude_code (PATH lookup + minimal
    # headless round-trip) so subscription auth problems surface here
    # instead of at first directive dispatch. Response shape matches
    # every other provider.
    if req.provider == "claude_code":
        ping_model = "claude-code:sonnet"
        if os.environ.get("KOMPANY_TEST_MODE", "") == "1":
            log.info("ping ok (test mode): provider=claude_code")
            return PingResponse(
                ok=True,
                model=ping_model,
                model_tested=ping_model,
                available_models=None,
                pricing=None,
                error_code=None,
                error_message=None,
            )
        detail = _ping_claude_code()
        if detail is not None:
            log.warning("ping failed: provider=claude_code detail=%s", detail)
            # A missing binary never reached the model — classify it as
            # provider_error explicitly (the generic classifier has no
            # marker for it) and leave model_tested unset.
            not_found = "not found on PATH" in detail
            return PingResponse(
                ok=False,
                model=None,
                model_tested=None if not_found else ping_model,
                available_models=None,
                pricing=None,
                error_code=(
                    "provider_error"
                    if not_found
                    else _classify_ping_error(detail)
                ),
                error_message=detail,
            )
        log.info("ping ok: provider=claude_code model=%s", ping_model)
        return PingResponse(
            ok=True,
            model=ping_model,
            model_tested=ping_model,
            available_models=None,
            pricing=None,
            error_code=None,
            error_message=None,
        )

    # For custom provider: discover models first so failures surface as
    # classified errors and the model used for the ping is recorded +
    # returned to the UI.
    model_override: str | None = None
    available_models: list[str] | None = None
    if req.provider == "custom":
        if not req.base_url:
            return PingResponse(
                ok=False,
                model=None,
                model_tested=None,
                available_models=None,
                pricing=None,
                error_code="provider_error",
                error_message="custom provider requires base_url",
            )
        try:
            available_models = _list_custom_models(req.base_url, req.api_key)
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            log.warning("custom /models list failed: %s", detail)
            return PingResponse(
                ok=False,
                model=None,
                model_tested=None,
                available_models=None,
                pricing=None,
                error_code=_classify_ping_error(detail),
                error_message=f"models.list failed: {detail}",
            )
        model_override = _pick_latest_custom_model(available_models)
        if not model_override:
            return PingResponse(
                ok=False,
                model=None,
                model_tested=None,
                available_models=available_models,
                pricing=None,
                error_code="provider_error",
                error_message="custom endpoint returned no models",
            )
        log.info(
            "custom ping: discovered %d models, testing with %s",
            len(available_models),
            model_override,
        )

    ok, detail = _ping_llm(
        req.provider,
        req.api_key,
        settings_factory=_settings_factory,
        model_override=model_override,
    )
    if not ok:
        log.warning(
            "ping failed: provider=%s model=%s detail=%s",
            req.provider,
            model_override or "(auto)",
            detail,
        )
        return PingResponse(
            ok=False,
            model=None,
            model_tested=model_override,
            available_models=available_models,
            pricing=None,
            error_code=_classify_ping_error(detail),
            error_message=detail,
        )

    # Success path: figure out the model that was actually pinged + its
    # pricing. Read pricing from the static ``llm.models.PRICING`` table.
    settings = _settings_factory()
    model = model_override or _ping_model_for_provider(req.provider, settings)
    log.info("ping ok: provider=%s model=%s", req.provider, model)
    pricing_entry = PRICING.get(model)
    pricing = (
        PingPricing(
            in_per_mtok=pricing_entry.input_per_mtok,
            out_per_mtok=pricing_entry.output_per_mtok,
        )
        if pricing_entry is not None
        else None
    )
    return PingResponse(
        ok=True,
        model=model,
        model_tested=model,
        available_models=available_models,
        pricing=pricing,
        error_code=None,
        error_message=None,
    )

