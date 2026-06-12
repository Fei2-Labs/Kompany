"""Kompany REST API — FastAPI interface to the engine."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
from pathlib import Path
from secrets import compare_digest
from typing import Any, AsyncIterator

from fastapi import BackgroundTasks, Body, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from kompany.core.engine import KompanyEngine
from kompany.core.event_hub import get_event_hub
from kompany.interfaces.mcp_bridge import router as mcp_bridge_router
from kompany.interfaces.web import render_dashboard
from kompany.remote import request_from_telegram_update

app = FastAPI(
    title="Kompany API",
    description="Autonomous business operating system for solo founders.",
    version="0.1.0",
)
app.include_router(mcp_bridge_router)

_engine: KompanyEngine | None = None


def get_engine() -> KompanyEngine:
    global _engine
    if _engine is None:
        _engine = KompanyEngine()
    return _engine


def reset_engine() -> None:
    """Drop the cached engine instance.

    Used by the onboarding REST endpoint after a fresh install so the
    next ``get_engine()`` call picks up the just-written ``kompany.db``.
    Also handy for tests that swap out data dirs across requests.
    """
    global _engine
    _engine = None


@app.on_event("startup")
async def _start_background_workers() -> None:
    """Start engine background workers (watchdog scanner + daemon ticker).

    ``KompanyEngine.start()`` existed but nothing ever awaited it in a
    server boot, so the watchdog's proactive scanner and the tick loop
    only ran in tests (which call ``scan_once``/``tick_once`` directly).
    Live-verification finding, 06-12-daemon-tick-loop.

    getattr-guarded: tests stub ``_engine`` with minimal fakes that have
    no worker surface, and the hook must not force every fake to grow one.
    """
    start = getattr(get_engine(), "start", None)
    if start is not None:
        await start()


@app.on_event("shutdown")
async def _stop_background_workers() -> None:
    stop = getattr(_engine, "stop", None)
    if stop is not None:
        await stop()


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe for the Tauri sidecar shell.

    The Rust shell polls this endpoint after spawning the sidecar
    binary and only opens the WebView once it returns 200. Keep it
    cheap — no DB hits, no engine spin-up.
    """
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Onboarding REST surface (used by the Tauri shell + browser flow).
# ---------------------------------------------------------------------------


class OnboardingStatusResponse(BaseModel):
    """Snapshot of whether the running install has been onboarded."""

    model_config = ConfigDict(extra="forbid")

    onboarded: bool
    template_id: str | None = None
    provider: str | None = None
    # Resume-from-review: when the wizard was interrupted between
    # SUBMIT TO TEAM and the founder acting on the team feasibility
    # review, the template is applied (onboarded=true) but the founder
    # still owes a keep/adopt/counter decision. The desktop shell uses
    # this id to land back on the wizard's review step instead of
    # dropping the founder on the dashboard (losing the LLM debate
    # they already paid for).
    pending_target_feasibility_approval_id: str | None = None
    agreed_targets_set: bool = False
    # Resume-to-step-5: agreed_targets are set, drafts exist, but no
    # active project yet → founder quit mid first-move. The shell drops
    # them back on the wizard's step 5 instead of the dashboard so the
    # generated directives aren't buried in inbox.
    pending_first_move: bool = False


class OnboardingCompleteRequest(BaseModel):
    """Body for ``POST /onboarding/complete`` sent by the in-window wizard."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    template_id: str = Field(..., min_length=1)
    directive: str | None = None
    base_url: str | None = None
    # Mission-targets task (05-19): the four quantitative onboarding
    # knobs. All optional — the engine falls back to the template
    # manifest's presets when these are missing.
    initial_budget: float | None = Field(default=None, ge=0.0)
    revenue_target: float | None = Field(default=None, ge=0.0)
    customer_target: int | None = Field(default=None, ge=0)
    deadline: str | None = None  # ISO 8601 string (YYYY-MM-DD ok)
    # Onboard-v2 task (05-19): founder-edited glossary term -> definition
    # overrides. Applied after the template's glossary is bulk-installed,
    # so a founder rewording "customer" lands on top of the template's
    # default definition. Forbidden-synonym lists are preserved.
    glossary_overrides: dict[str, str] | None = None


class OnboardingCompleteResponse(BaseModel):
    """Response from ``POST /onboarding/complete``."""

    model_config = ConfigDict(extra="forbid")

    status: str  # "ready" | "error"
    template_id: str | None = None
    provider: str | None = None
    message: str | None = None
    code: str | None = None
    # Approval id of the team's feasibility review (when one fired).
    targets_review_id: str | None = None


class PingPricing(BaseModel):
    """Per-million-token pricing for the model used in a connectivity probe."""

    model_config = ConfigDict(extra="forbid")

    in_per_mtok: float
    out_per_mtok: float


class PingRequest(BaseModel):
    """Body for ``POST /onboarding/ping`` — fail-fast API key validation."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    base_url: str | None = None


class PingResponse(BaseModel):
    """Outcome of a single connectivity probe against an LLM provider."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    model: str | None = None
    model_tested: str | None = None
    available_models: list[str] | None = None
    pricing: PingPricing | None = None
    # One of: unauthorized | rate_limited | network | provider_error | unknown
    error_code: str | None = None
    error_message: str | None = None


class EnvDefaultsResponse(BaseModel):
    """Environment-supplied defaults the onboarding wizard pre-fills."""

    model_config = ConfigDict(extra="forbid")
    custom_base_url: str = ""
    # Full key returned — same machine, user controls .env. The wizard
    # masks all-but-last-4 in the input rendering; the raw value is
    # POSTed back on submit so the founder doesn't need to retype.
    custom_api_key: str = ""
    # Provider hint: "custom" if base_url + api_key both set, else "".
    suggested_provider: str = ""
    suggested_model: str = ""


def _parse_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE .env parser (no python-dotenv dependency so it
    survives PyInstaller bundling). Ignores blank lines, ``#`` comments,
    and strips surrounding quotes. Last value wins on duplicate keys."""
    out: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                out[key] = val
    except (OSError, UnicodeDecodeError):
        pass
    return out


def _env_lookup() -> dict[str, str]:
    """Merge the process environment with a ``.env`` file in the data
    dir.

    A GUI app launched from Finder on macOS does NOT inherit the shell
    environment, so a ``.env`` sitting in the dev project root is
    invisible to the installed app. To make the auto-fill work for
    desktop founders, we also read ``<data_dir>/.env``. Process env
    wins over the file (explicit override).
    """
    merged = dict(_parse_env_file(_resolved_data_dir() / ".env"))
    merged.update({k: v for k, v in os.environ.items()})
    return merged


@app.get("/onboarding/env_defaults", response_model=EnvDefaultsResponse)
def onboarding_env_defaults() -> EnvDefaultsResponse:
    """Return any pre-filled values the wizard should display in step 1.

    Reads from the process environment AND ``<data_dir>/.env`` so the
    founder doesn't have to re-type a custom-LLM base URL + key they
    already configured. The data-dir file is necessary because a
    Finder-launched desktop app doesn't inherit the shell environment.
    Returns empty strings when nothing is set.
    """
    env = _env_lookup()
    base = env.get("CUSTOM_LLM_BASE_URL", "").strip()
    key = env.get("CUSTOM_LLM_API_KEY", "").strip()
    # Model hint: prefer KOMPANY_MODEL_PRIMARY, fall back to APEX.
    model = (
        env.get("KOMPANY_MODEL_PRIMARY", "").strip()
        or env.get("KOMPANY_MODEL_APEX", "").strip()
    )
    suggested = "custom" if (base and key) else ""
    return EnvDefaultsResponse(
        custom_base_url=base,
        custom_api_key=key,
        suggested_provider=suggested,
        suggested_model=model,
    )


# ---------------------------------------------------------------------------
# Mid-onboarding credential stash — persist the founder's API key to the
# encrypted vault as soon as step 1 pings OK, so closing the wizard
# mid-flow doesn't lose it. Same encryption + file as the final
# onboarding-complete write; this just happens a few steps earlier.
# ---------------------------------------------------------------------------

_PENDING_PROVIDER_KEY = "onboarding.pending_provider"


class StashCredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    base_url: str | None = None


class StashCredentialsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stored: bool = False
    storage: str = ""  # "vault" | "none"
    note: str = ""


class StashedCredentialsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    has_key: bool = False


@app.post(
    "/onboarding/stash_credentials",
    response_model=StashCredentialsResponse,
)
def onboarding_stash_credentials(
    req: StashCredentialsRequest,
) -> StashCredentialsResponse:
    """Persist the founder's provider + API key to the encrypted vault
    mid-onboarding (after a successful step-1 ping).

    Security note: this writes the SAME encrypted credential_vault row
    that onboarding-complete would write, using the same file-based
    Fernet master key. There is no new exposure — the key just lands
    on disk (encrypted) a few wizard steps earlier so a mid-flow quit
    doesn't force the founder to re-enter it. We never write the key
    to localStorage / plaintext.
    """
    from kompany.installer.onboard import PROVIDER_VAULT_KEYS

    engine = get_engine()
    vault_field = PROVIDER_VAULT_KEYS.get(req.provider)
    if not vault_field:
        return StashCredentialsResponse(
            stored=False, storage="none", note="unknown provider"
        )
    if not getattr(engine.settings, "vault_key", ""):
        return StashCredentialsResponse(
            stored=False, storage="none", note="vault key not configured"
        )
    try:
        engine.credentials.set(vault_field, req.api_key)
        if req.provider == "custom" and req.base_url:
            engine.credentials.set("custom_base_url", req.base_url.strip())
        # Remember which provider the founder picked so resume can
        # restore the right slot.
        engine.db.execute(
            """INSERT INTO company_config (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value, updated_at = excluded.updated_at""",
            (_PENDING_PROVIDER_KEY, req.provider),
        )
        engine.db.commit()
    except Exception as exc:  # noqa: BLE001
        return StashCredentialsResponse(
            stored=False, storage="none", note=f"vault write failed: {exc}"
        )
    return StashCredentialsResponse(stored=True, storage="vault")


@app.get(
    "/onboarding/stashed_credentials",
    response_model=StashedCredentialsResponse,
)
def onboarding_stashed_credentials() -> StashedCredentialsResponse:
    """Return credentials stashed during a prior (incomplete) onboarding
    so the wizard can restore them after a mid-flow quit + relaunch.

    Returns the decrypted key (same machine, the founder owns it) so
    the wizard can repopulate the password field and complete without
    re-entry. Empty when nothing was stashed.
    """
    engine = get_engine()
    row = engine.db.execute(
        "SELECT value FROM company_config WHERE key = ?",
        (_PENDING_PROVIDER_KEY,),
    ).fetchone()
    provider = (row["value"] if row else "") or ""
    if not provider:
        return StashedCredentialsResponse()

    from kompany.installer.onboard import PROVIDER_VAULT_KEYS

    vault_field = PROVIDER_VAULT_KEYS.get(provider)
    api_key = ""
    base_url = ""
    if vault_field:
        try:
            api_key = engine.credentials.get(vault_field) or ""
        except Exception:  # noqa: BLE001
            api_key = ""
    if provider == "custom":
        try:
            base_url = engine.credentials.get("custom_base_url") or ""
        except Exception:  # noqa: BLE001
            base_url = ""
    return StashedCredentialsResponse(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        has_key=bool(api_key),
    )


def _resolved_data_dir() -> Path:
    """Resolve the data dir the sidecar should use, consistent with engine."""
    env = os.environ.get("KOMPANY_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return Path("~/.kompany").expanduser()


@app.get("/onboarding/status", response_model=OnboardingStatusResponse)
def onboarding_status() -> OnboardingStatusResponse:
    """Report whether onboarding has completed for the current data dir.

    Read-only and safe to call before any engine spin-up — the Tauri
    shell hits this on every WebView load so the SPA can redirect to
    the in-window wizard when no template has been applied yet.

    Also surfaces the resume signal: if a ``target_feasibility``
    approval is still pending OR ``targets.agreed`` is unset, the
    desktop / web shell should drop the founder on the wizard's
    review step instead of the dashboard. Otherwise the LLM debate
    they already paid for is buried in the inbox.
    """
    from kompany.installer import is_onboarded

    snap = is_onboarded(_resolved_data_dir())
    resp_kwargs: dict[str, Any] = dict(snap)

    # Probe the DB directly (no engine spin-up) for the resume signal.
    # Pre-onboarded installs simply return None / False, matching the
    # default response.
    if snap.get("onboarded"):
        import sqlite3

        db_path = _resolved_data_dir().expanduser() / "kompany.db"
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT id FROM approval_requests "
                    "WHERE action_type = 'target_feasibility' "
                    "AND status = 'pending' "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row:
                    resp_kwargs["pending_target_feasibility_approval_id"] = row["id"]
            except sqlite3.OperationalError:
                pass
            try:
                row = conn.execute(
                    "SELECT value FROM company_config WHERE key = 'targets.agreed'"
                ).fetchone()
                resp_kwargs["agreed_targets_set"] = bool(row and row["value"])
            except sqlite3.OperationalError:
                pass
            # Resume-to-step-5 signal: the founder agreed targets and has
            # NOT yet made a first move. True only when drafts exist AND
            # no project has progressed past draft — i.e. nothing active
            # AND nothing completed. Once a first directive has run (even
            # to completion), the founder is "live": leftover unpicked
            # drafts must NOT drag them back to step 5; they belong on the
            # dashboard. (Bug 2026-05-27: a completed first directive +
            # leftover drafts re-triggered step 5.)
            if resp_kwargs.get("agreed_targets_set"):
                try:
                    drafts = conn.execute(
                        "SELECT COUNT(*) AS n FROM projects WHERE status = 'draft'"
                    ).fetchone()
                    progressed = conn.execute(
                        "SELECT COUNT(*) AS n FROM projects "
                        "WHERE status IN ('active', 'completed', 'paused', 'failed')"
                    ).fetchone()
                    n_drafts = int(drafts["n"]) if drafts else 0
                    n_progressed = int(progressed["n"]) if progressed else 0
                    resp_kwargs["pending_first_move"] = bool(
                        n_drafts > 0 and n_progressed == 0
                    )
                except sqlite3.OperationalError:
                    pass
            conn.close()
        except sqlite3.Error:
            pass

    return OnboardingStatusResponse(**resp_kwargs)


@app.post(
    "/onboarding/complete",
    response_model=OnboardingCompleteResponse,
)
def onboarding_complete(req: OnboardingCompleteRequest) -> OnboardingCompleteResponse:
    """Run a fully-headless onboard from the in-window wizard form.

    On success the cached engine is dropped so a follow-up ``/status``
    or ``/agents/status`` request rebuilds against the freshly-written
    ``kompany.db``. Errors are surfaced as ``status='error'`` with a
    short message rather than a 5xx, so the JS form can show them
    inline without parsing FastAPI's error envelope.
    """
    from kompany.installer import OnboardError, onboard_headless

    try:
        result = onboard_headless(
            data_dir=_resolved_data_dir(),
            provider=req.provider,
            api_key=req.api_key,
            template_id=req.template_id,
            directive=req.directive,
            base_url=req.base_url,
            initial_budget=req.initial_budget,
            revenue_target=req.revenue_target,
            customer_target=req.customer_target,
            deadline=req.deadline,
            glossary_overrides=req.glossary_overrides,
        )
    except OnboardError as exc:
        return OnboardingCompleteResponse(
            status="error",
            message=exc.message,
            code=exc.code,
        )

    # Drop the cached engine so the next request rebuilds against the
    # freshly-initialised data dir.
    reset_engine()
    return OnboardingCompleteResponse(
        status="ready",
        template_id=result.template_id,
        provider=result.provider,
        message=None,
        targets_review_id=result.targets_review_id,
    )


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


@app.post("/onboarding/ping", response_model=PingResponse)
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


class DirectiveRequest(BaseModel):
    text: str
    # CEO-channel session to continue (06-03-ceo-channel). Optional: omitting
    # it opens a fresh session, preserving the legacy one-shot /directive
    # behaviour. A clarify reply passes the session_id from the prior result.
    session_id: str | None = None


class OverrideRequest(BaseModel):
    text: str


class DecisionPacketRequest(BaseModel):
    text: str
    target_amount: float | None = None


class InitRequest(BaseModel):
    name: str
    capital: float = 0.0
    goal: str = ""
    time_horizon: str = ""
    exclusions: str = ""


class DebateRequest(BaseModel):
    question: str


class RejectApprovalRequest(BaseModel):
    reason: str = ""
    comment: str = ""


class ApproveApprovalRequest(BaseModel):
    comment: str = ""


class ReviseApprovalRequest(BaseModel):
    counter: str
    comment: str = ""


class SnoozeApprovalRequest(BaseModel):
    minutes: int
    comment: str = ""


class CancelApprovalRequest(BaseModel):
    reason: str = ""
    comment: str = ""


class CommentApprovalRequest(BaseModel):
    body: str
    by_type: str = "user"
    by_id: str | None = None


class HeartbeatRequest(BaseModel):
    dispatch: bool = False
    adapter: str = "dry-run"


class DispatchNotificationsRequest(BaseModel):
    events: list[dict[str, Any]]
    adapter: str = "dry-run"


class ToolPolicyRequest(BaseModel):
    agent_role: str
    tool_name: str
    allowed: bool
    requires_approval: bool = False
    reason: str = ""


class ToolAuthorizationRequest(BaseModel):
    agent_role: str
    tool_name: str
    purpose: str = ""
    arguments: dict[str, Any] = {}
    approval_id: str | None = None


class RemoteCommandAPIRequest(BaseModel):
    source: str = "mobile"
    text: str
    chat_id: str = ""
    bearer_token: str = ""
    payload: dict[str, Any] = {}


class RemoteReplayCleanupRequest(BaseModel):
    ttl_seconds: int | None = None


class DashboardActionRequest(BaseModel):
    action: str
    approval_id: str | None = None
    reason: str = ""


class CredentialRequest(BaseModel):
    name: str
    value: str


class CredentialKeyRotationRequest(BaseModel):
    new_vault_key: str


@app.post("/init")
def init_company(req: InitRequest) -> dict[str, Any]:
    """Initialize a new Kompany."""
    engine = get_engine()
    engine.initialize_company(
        name=req.name,
        capital=req.capital,
        goal=req.goal,
        time_horizon=req.time_horizon,
        exclusions=req.exclusions,
    )
    return {
        "status": "initialized",
        "name": req.name,
        "capital": req.capital,
        "goal": req.goal,
        "time_horizon": req.time_horizon,
        "exclusions": req.exclusions,
        "stage": "solo",
    }


class ModelSettingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_model: str = ""
    provider: str = ""
    base_url: str = ""
    available_models: list[str] = []
    error: str = ""


class SetModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(..., min_length=1)


@app.get("/settings/model", response_model=ModelSettingResponse)
def get_model_setting() -> ModelSettingResponse:
    """Current model + the models the configured endpoint advertises.

    Lets the founder switch model from the UI when their provider drops
    a model (e.g. swedeapi's gpt-5.x went down mid-session and the only
    fix was hand-editing .env). For custom endpoints we query the live
    /models list; other providers return their tier model with no list.
    """
    engine = get_engine()
    s = engine.settings
    current = getattr(s, "model_primary", "") or ""
    base_url = getattr(s, "custom_base_url", "") or ""
    available: list[str] = []
    err = ""
    provider = "custom" if base_url else "default"
    if base_url:
        try:
            from kompany.llm.providers import list_openai_compatible_models
            available = list_openai_compatible_models(
                base_url, getattr(s, "custom_api_key", "") or ""
            )
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
    return ModelSettingResponse(
        current_model=current, provider=provider, base_url=base_url,
        available_models=available, error=err,
    )


@app.post("/settings/model", response_model=ModelSettingResponse)
def set_model_setting(req: SetModelRequest) -> ModelSettingResponse:
    """Switch the model for all three tiers. Persists to company_config
    (``custom_model_picked``, the same key the engine applies on boot)
    AND updates the live engine so the change takes effect immediately —
    no restart needed."""
    engine = get_engine()
    model = req.model.strip()
    engine.db.execute(
        """INSERT INTO company_config (key, value, updated_at)
           VALUES ('custom_model_picked', ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET
             value = excluded.value, updated_at = excluded.updated_at""",
        (model,),
    )
    engine.db.commit()
    engine.settings.model_apex = model
    engine.settings.model_primary = model
    engine.settings.model_economy = model
    engine.audit.record(
        "settings.model_changed",
        f"Founder switched model to {model}",
        detail={"model": model},
    )
    return get_model_setting()


class ModelSourceRequest(BaseModel):
    """Body for ``PUT /settings/model-source``.

    ``kind=None`` clears the source (legacy per-token billing). The
    execution loop is derived by the engine — there is deliberately no
    vehicle/runner input here (PRD 06-11 D1).
    """

    model_config = ConfigDict(extra="forbid")
    kind: str | None = None
    billing_mode: str | None = None
    monthly_fee_usd: float | None = None
    price_overrides: dict[str, tuple[float, float]] | None = None


class SelfUpdateProposeRequest(BaseModel):
    """Body for ``POST /self-update/propose``."""

    instruction: str


@app.post("/self-update/propose")
def self_update_propose(req: SelfUpdateProposeRequest) -> dict:
    """Governed self-update propose flow (06-12-self-update-pipeline)."""
    try:
        return get_engine().self_update_propose(req.instruction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/self-update/proposals")
def self_update_proposals(limit: int = 20) -> list[dict]:
    """Recent self-update proposals, newest first."""
    return get_engine().self_update_list(limit=limit)


@app.get("/self-update/proposals/{proposal_id}")
def self_update_proposal(proposal_id: str) -> dict:
    row = get_engine().self_update_show(proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return row


@app.get("/settings/model-source")
def get_model_source_setting() -> dict | None:
    """Active model source (or null). Same dict shape as SDK/MCP."""
    return get_engine().get_model_source()


@app.put("/settings/model-source")
def set_model_source_setting(req: ModelSourceRequest) -> dict:
    """Set or clear the active model source; persists to the settings YAML."""
    payload: dict | None = None
    if req.kind is not None:
        payload = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        return get_engine().set_model_source(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/settings/detect-clis")
def detect_clis_setting() -> dict:
    """Probe PATH for agent CLIs that unlock zero-key model sources."""
    return get_engine().detect_agent_clis()


class IntegrationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    integration_id: str
    display_name: str
    required_credentials: list[str]
    connected: bool


class ConnectEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    smtp_host: str = Field(..., min_length=1)
    smtp_port: str = "587"
    smtp_user: str = Field(..., min_length=1)
    smtp_password: str = Field(..., min_length=1)
    smtp_from: str = ""


class IntegrationActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    detail: str = ""


@app.get("/integrations", response_model=list[IntegrationInfo])
def list_integrations() -> list[IntegrationInfo]:
    """List built-in integrations + whether the founder has connected
    each (all required credentials present in the vault)."""
    from kompany.integrations.email_smtp import EmailIntegration, ResendIntegration

    engine = get_engine()
    out: list[IntegrationInfo] = []
    for integ in (EmailIntegration(), ResendIntegration()):
        creds = integ.required_credentials
        connected = bool(creds) and all(
            (engine.credentials.get(c) or "") for c in creds
        )
        out.append(IntegrationInfo(
            integration_id=integ.integration_id,
            display_name=integ.display_name,
            required_credentials=list(creds),
            connected=connected,
        ))
    return out


@app.post("/integrations/email/connect", response_model=IntegrationActionResponse)
def connect_email(req: ConnectEmailRequest) -> IntegrationActionResponse:
    """Store SMTP credentials in the vault + verify them with a login.

    This is the founder's job #1 (connect accounts) — once stored, the
    team can actually send email. Verifies before saving so a bad
    password is caught now, not at send time."""
    import smtplib
    import ssl

    engine = get_engine()
    if not getattr(engine.settings, "vault_key", ""):
        return IntegrationActionResponse(ok=False, detail="vault key not configured")
    host, port = req.smtp_host.strip(), int(req.smtp_port or "587")
    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
                s.login(req.smtp_user, req.smtp_password)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=ctx)
                s.login(req.smtp_user, req.smtp_password)
    except Exception as exc:  # noqa: BLE001
        return IntegrationActionResponse(ok=False, detail=f"login failed: {type(exc).__name__}: {exc}")
    engine.credentials.set("smtp_host", host)
    engine.credentials.set("smtp_port", str(port))
    engine.credentials.set("smtp_user", req.smtp_user.strip())
    engine.credentials.set("smtp_password", req.smtp_password)
    engine.credentials.set("smtp_from", (req.smtp_from or req.smtp_user).strip())
    engine.audit.record("integration.connected", "Connected email (SMTP)",
                        detail={"integration": "email_smtp", "host": host})
    return IntegrationActionResponse(ok=True, detail=f"connected {req.smtp_user} @ {host}:{port}")


class IntegrationCredsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")  # arbitrary string fields


@app.get("/integrations/{integration_id}/credentials")
def get_integration_credentials(integration_id: str) -> dict[str, Any]:
    """Return the founder's stored credentials for one integration so
    the Settings form can pre-fill on page load. Secrets are MASKED
    (last 4 chars only) — non-secret fields (from address, host, user,
    port) come back in full so the founder sees what's saved."""
    engine = get_engine()
    fields_by_id = {
        "email_smtp": (
            ("smtp_host", False), ("smtp_port", False), ("smtp_user", False),
            ("smtp_password", True), ("smtp_from", False),
        ),
        "resend": (("resend_api_key", True), ("resend_from", False)),
    }
    cfg = fields_by_id.get(integration_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"unknown integration: {integration_id}")
    out: dict[str, Any] = {}
    for name, is_secret in cfg:
        v = engine.credentials.get(name) or ""
        if is_secret and v:
            out[name + "_mask"] = "•" * 6 + v[-4:]
            out[name + "_set"] = True
        else:
            out[name] = v
            if is_secret:
                out[name + "_set"] = False
    return out


class ConnectResendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Empty string means "keep the saved key" — lets the founder change
    # the From without re-pasting the key every time.
    api_key: str = ""
    resend_from: str = Field(..., min_length=1)


@app.post("/integrations/resend/connect", response_model=IntegrationActionResponse)
def connect_resend(req: ConnectResendRequest) -> IntegrationActionResponse:
    """Store + verify a Resend API key. Verifies by listing domains
    (validates the key) before saving."""
    import urllib.error
    import urllib.request

    engine = get_engine()
    if not getattr(engine.settings, "vault_key", ""):
        return IntegrationActionResponse(ok=False, detail="vault key not configured")
    api_key = req.api_key.strip()
    if not api_key:
        api_key = engine.credentials.get("resend_api_key") or ""
    if not api_key:
        return IntegrationActionResponse(ok=False, detail="api key required")
    # Verify auth, NOT scope: a Resend "sending access" key (the right
    # kind for an app) returns 403 on /domains because it lacks
    # domains-read permission — but it CAN send. So 200 and 403 both
    # mean "key authenticates"; only 401 = invalid key.
    try:
        vr = urllib.request.Request(
            "https://api.resend.com/domains",
            headers={
                "Authorization": f"Bearer {api_key}",
                # Cloudflare fronts api.resend.com and 403s requests with
                # the default urllib UA as "error code: 1010".
                "User-Agent": "Kompany/0.1 (+https://kompany.dev)",
            },
        )
        urllib.request.urlopen(vr, timeout=30).read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return IntegrationActionResponse(ok=False, detail="Resend rejected the key (401 — invalid)")
        # 403 (restricted scope) or other auth-passing codes → accept;
        # the send test will surface any real send-time problem.
    except Exception as exc:  # noqa: BLE001
        return IntegrationActionResponse(ok=False, detail=f"verify failed: {type(exc).__name__}: {exc}")
    engine.credentials.set("resend_api_key", api_key)
    engine.credentials.set("resend_from", req.resend_from.strip())
    engine.audit.record("integration.connected", "Connected Resend",
                        detail={"integration": "resend", "from": req.resend_from})
    return IntegrationActionResponse(ok=True, detail=f"connected Resend (from {req.resend_from})")


class ProposeEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)


@app.post("/integrations/email/propose")
def propose_email(req: ProposeEmailRequest) -> dict[str, Any]:
    """Queue an email send for founder approval (does NOT send now).

    Demonstrates the deferred-action pipeline: the proposal lands in the
    inbox; approving it (POST /approvals/{id}/approve) actually sends.
    This is how an agent will hand off an external action — propose,
    founder approves, it happens."""
    engine = get_engine()
    return engine.propose_action(
        "email.send",
        {"to": req.to, "subject": req.subject, "body": req.body},
        summary=f"Send email to {req.to}: {req.subject}",
        severity="medium",
    )


class TestEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Optional. Empty → send to the connected From address (self-test).
    # Set a different inbox to prove real external delivery, not loopback.
    to: str = Field("", description="recipient; defaults to the connected From address")


@app.post("/integrations/email/test", response_model=IntegrationActionResponse)
def test_email(req: TestEmailRequest | None = Body(default=None)) -> IntegrationActionResponse:
    """Send a test email (proves real sending).

    Default recipient is the connected From address (self-test). The
    founder can override ``to`` to send to a different inbox — that
    separates "did the send succeed" from "does my From mailbox receive".
    """
    from kompany.integrations.email_smtp import SendEmailTool, SendEmailInput
    from kompany.plugins.contract import ToolContext

    engine = get_engine()
    sender = (
        engine.credentials.get("resend_from")
        or engine.credentials.get("smtp_from")
        or engine.credentials.get("smtp_user")
    )
    if not sender:
        return IntegrationActionResponse(ok=False, detail="email not connected")
    to = (req.to.strip() if req and req.to else "") or sender
    ctx = ToolContext(
        run_id="", ledger=engine.ledger, audit=engine.audit,
        credentials=engine.credentials, settings=engine.settings,
    )
    out = SendEmailTool().execute(
        SendEmailInput(to=to, subject="Kompany test email",
                       body="Your Kompany team can now send email. ✅"),
        ctx,
    )
    return IntegrationActionResponse(ok=out.sent, detail=out.detail)


# ---------------------------------------------------------------------------
# CEO channel: founder↔team conversation surface (06-03-ceo-channel)
#
# The directive bar becomes the CEO channel. ``/channel/send`` is the canonical
# entry; ``/directive`` is kept for backward compat and delegates to the SAME
# handler path (no behaviour change for existing callers). All directive-result
# responses flow through one flattener so SDK/REST stay key-identical.
# ---------------------------------------------------------------------------


def _flatten_directive_result(result: Any) -> dict[str, Any]:
    """Serialize a ``DirectiveResult`` to the shared parity dict.

    Delegates to ``DirectiveResult.to_dict()`` — the single source of truth
    shared by CLI/MCP/SDK so every non-web surface emits identical top-level
    keys (status / message / project_id / approval_id / total_ai_cost /
    agents_used / run_id / session_id). Used by /directive, /channel/send,
    /channel/*/go and /channel/*/abandon.
    """
    return result.to_dict()


def _process_directive_graceful(text: str, session_id: str | None) -> dict[str, Any]:
    """Run ``process_directive`` wrapped in the graceful-error contract.

    ``process_directive`` re-raises on LLM failure (its library contract).
    The HTTP layer must NOT surface that as a raw 500 — the founder sees
    "directive failed: HTTP 500" with no clue. Instead we catch it and
    return a graceful 200 with a classified, honest error (reusing
    :func:`_classify_ping_error`) so the live timeline / channel thread can
    render the real cause (quota / auth / network / provider).

    Shared by ``/directive`` and ``/channel/send`` — single source of truth,
    no duplicated error logic (code-reuse spec).
    """
    engine = get_engine()
    try:
        result = engine.process_directive(text, session_id=session_id)
    except Exception as exc:  # noqa: BLE001 — surface honestly, never 500
        detail = f"{type(exc).__name__}: {exc}"
        code = _classify_ping_error(detail)
        human = {
            "rate_limited": "Your LLM provider hit a rate/quota limit. Wait a moment and retry.",
            "unauthorized": "Your LLM provider rejected the API key. Check it in settings.",
            "network": "Couldn't reach your LLM provider. Check your connection and retry.",
            "provider_error": "Your LLM provider rejected the request (it may not support this model/params). Try a different model in settings.",
        }.get(code, "The team's LLM call failed. Retry, or switch model/provider in settings.")
        return {
            "status": "failed",
            "message": human,
            "error_code": code,
            "project_id": None,
            "approval_id": None,
            "total_ai_cost": 0.0,
            "agents_used": [],
            "run_id": None,
            "session_id": session_id,
        }
    return _flatten_directive_result(result)


@app.post("/directive")
def send_directive(req: DirectiveRequest) -> dict[str, Any]:
    """Send a directive to Kompany (backward-compat alias for /channel/send).

    Delegates to the same handler path as ``/channel/send`` so existing
    callers (CLI/SDK/MCP/internal) keep working unchanged: the response keeps
    the legacy keys plus session_id/run_id (added in PR0/PR1). No duplicated
    logic — both routes funnel through :func:`_process_directive_graceful`.
    """
    return _process_directive_graceful(req.text, req.session_id)


class ChannelSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    # Continue an existing session (clarify reply / gated context); omit to
    # open a fresh session.
    session_id: str | None = None


@app.post("/channel/send")
def channel_send(req: ChannelSendRequest) -> dict[str, Any]:
    """Founder message into the CEO channel.

    Opens a new session (``session_id`` omitted) or continues an existing one
    (clarify reply). Returns the full flattened ``DirectiveResult`` — status
    may be ``clarify`` (CEO asks back), ``gated`` (threshold gate, awaiting
    GO), or any execute/answer status. LLM failures surface as a graceful 200
    with a classified ``error_code``, never a 500.
    """
    return _process_directive_graceful(req.text, req.session_id)


def _session_to_dict(session: Any) -> dict[str, Any]:
    """Serialize a ``ConversationSession`` row for the channel REST surface."""
    return {
        "session_id": session.id,
        "state": session.state.value,
        "route": session.route,
        "clarify_turns": session.clarify_turns,
        "created_at": str(session.created_at) if session.created_at is not None else None,
        "closed_at": str(session.closed_at) if session.closed_at is not None else None,
        "run_id": session.run_id,
        "directive_id": session.directive_id,
        "project_id": session.project_id,
        "approval_id": session.approval_id,
    }


def _turn_to_dict(turn: Any) -> dict[str, Any]:
    """Serialize a ``ConversationTurn`` row for the channel thread view."""
    return {
        "turn_index": turn.turn_index,
        "role": turn.role,
        "content": turn.content,
        "kind": turn.kind,
        "cost": turn.cost,
        "run_id": turn.run_id,
        "directive_id": turn.directive_id,
        "created_at": str(turn.created_at) if turn.created_at is not None else None,
    }


@app.get("/channel/sessions")
def channel_sessions(
    state: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List CEO-channel sessions, newest first.

    Optional ``state`` filters by session lifecycle state (open / clarifying /
    gated / dispatched / answered / abandoned); ``limit`` caps the result set
    (sane default 50). Used by the Web UI to restore the thread list on load.
    """
    engine = get_engine()
    limit = max(1, min(int(limit), 200))
    sessions = engine.channel.list_sessions(state=state, limit=limit)
    return {"sessions": [_session_to_dict(s) for s in sessions]}


@app.get("/channel/sessions/{session_id}")
def channel_session_detail(session_id: str) -> dict[str, Any]:
    """A single session plus its ordered turns (the full thread).

    404 if the session is unknown. The thread restores in-flight turn state on
    reload (no SSE replay) — clients reconcile live cost via
    ``/channel/runs/{run_id}/cost``.
    """
    engine = get_engine()
    session = engine.channel.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown channel session {session_id!r}")
    turns = engine.channel.session_turns(session_id)
    return {
        "session": _session_to_dict(session),
        "turns": [_turn_to_dict(t) for t in turns],
    }


@app.post("/channel/sessions/{session_id}/go")
def channel_go(session_id: str) -> dict[str, Any]:
    """Founder GO on a gated session — execute the held directive.

    Returns the flattened ``DirectiveResult``. The engine returns a clean
    failed result (no exceptions) for illegal GOs (non-gated / closed /
    unknown), so this never 500s.
    """
    engine = get_engine()
    return _flatten_directive_result(engine.channel_go(session_id))


@app.post("/channel/sessions/{session_id}/abandon")
def channel_abandon(session_id: str) -> dict[str, Any]:
    """Founder abandons a session — close it without executing.

    Returns the flattened ``DirectiveResult``. The engine returns a clean
    failed result (no exceptions) for non-abandonable sessions.
    """
    engine = get_engine()
    return _flatten_directive_result(engine.channel_abandon(session_id))


@app.get("/channel/runs/{run_id}/cost")
def channel_run_cost(run_id: str) -> dict[str, Any]:
    """Authoritative per-run AI cost — reconcile source for reload restore.

    SSE has no replay, so a mid-execution reload misses ``llm.spend`` events
    emitted while the page was loading. The channel bubble restores the
    accumulated cost by summing the ledger's ``ai_cost`` rows for this
    ``run_id`` (each ledger row carries ``run_id``). Returned as a positive
    total (AI-cost rows are stored as negative expense amounts).
    """
    engine = get_engine()
    row = engine.db.execute(
        "SELECT COALESCE(SUM(ABS(amount)), 0.0) AS total "
        "FROM ledger WHERE run_id = ? AND category = 'ai_cost'",
        (run_id,),
    ).fetchone()
    total = float(row["total"]) if row and row["total"] is not None else 0.0
    return {"run_id": run_id, "total_cost": total}


@app.post("/override")
def request_override(req: OverrideRequest) -> dict[str, Any]:
    """Request an override with a risk briefing."""
    engine = get_engine()
    return engine.process_override(req.text)


@app.post("/decision-packet")
def prepare_decision_packet(req: DecisionPacketRequest) -> dict[str, Any]:
    """Prepare a full decision-chain packet without executing it."""
    engine = get_engine()
    return engine.prepare_decision_packet(req.text, target_amount=req.target_amount)


@app.post("/projects/{project_id}/resume")
def resume_project(project_id: str) -> dict[str, Any]:
    """Resume a project from persisted task/checkpoint state."""
    engine = get_engine()
    try:
        return engine.resume_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ExecutePacketRequest(BaseModel):
    approval_id: str


@app.post("/decision-packet/execute")
def execute_decision_packet(req: ExecutePacketRequest) -> dict[str, Any]:
    """Execute an approved decision-chain packet under governance."""
    engine = get_engine()
    try:
        return engine.execute_decision_packet(req.approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ReleaseDeliveryRequest(BaseModel):
    approval_id: str


@app.post("/delivery/release")
def release_delivery(req: ReleaseDeliveryRequest) -> dict[str, Any]:
    """Release a delivery package after delivery_approval is approved."""
    engine = get_engine()
    try:
        return engine.release_delivery(req.approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class RetrospectiveRequest(BaseModel):
    project_id: str


@app.post("/retrospective")
def run_retrospective(req: RetrospectiveRequest) -> dict[str, Any]:
    """Run or replay a CoS retrospective for a project."""
    engine = get_engine()
    return engine.run_retrospective(req.project_id)


class HealthResolveRequest(BaseModel):
    action: str
    snooze_minutes: int | None = None
    resolved_by: str = "player"


@app.get("/health/events")
def list_health_events(
    status: str | None = None,
    kind: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List watchdog health events, newest-first."""
    engine = get_engine()
    try:
        return engine.list_health_events(
            status=status,
            kind=kind,
            project_id=project_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/health/events/{event_id}")
def get_health_event(event_id: str) -> dict[str, Any]:
    """Fetch one health event."""
    engine = get_engine()
    row = engine.get_health_event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Health event not found: {event_id}")
    return row


@app.post("/health/events/{event_id}/resolve")
def resolve_health_event(
    event_id: str,
    req: HealthResolveRequest,
) -> dict[str, Any]:
    """Apply a player action (``continue`` / ``snooze`` / ``dismiss``)."""
    engine = get_engine()
    try:
        row = engine.resolve_health_event(
            event_id=event_id,
            action=req.action,
            snooze_minutes=req.snooze_minutes,
            resolved_by=req.resolved_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"Health event not found: {event_id}")
    return row


class TemplateApplyRequest(BaseModel):
    force: bool = False
    override_budget: float | None = None
    override_directive: str | None = None


@app.get("/templates")
def list_templates() -> list[dict[str, Any]]:
    """List available company templates."""
    return get_engine().list_templates()


@app.get("/templates/{template_id}")
def get_template(template_id: str) -> dict[str, Any]:
    """Fetch one template by id (includes rendered mission body)."""
    try:
        return get_engine().show_template(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/templates/{template_id}/apply")
def apply_template(
    template_id: str,
    req: TemplateApplyRequest | None = None,
) -> dict[str, Any]:
    """Apply a template — writes company config, ledgers the initial
    budget, and stages suggested directives as draft projects."""
    body = req or TemplateApplyRequest()
    try:
        return get_engine().apply_template(
            template_id,
            force=body.force,
            override_budget=body.override_budget,
            override_directive=body.override_directive,
        )
    except ValueError as exc:
        # Distinguish "not found" from "already applied" via message
        message = str(exc)
        status = 404 if "not found" in message else 409
        raise HTTPException(status_code=status, detail=message) from exc


# ---------------------------------------------------------------------------
# Company targets (mission-targets task 05-19)
# ---------------------------------------------------------------------------


class UIPreferencesResponse(BaseModel):
    """Founder's dashboard appearance preferences."""

    model_config = ConfigDict(extra="forbid")

    theme_id: str
    auto_enabled: bool
    reduce_motion: str  # "auto" | "on" | "off"


class UIPreferencesUpdateRequest(BaseModel):
    """Partial update for ``PATCH /preferences`` — omitted fields are untouched."""

    model_config = ConfigDict(extra="forbid")

    theme_id: str | None = None
    auto_enabled: bool | None = None
    reduce_motion: str | None = None


@app.get("/preferences", response_model=UIPreferencesResponse)
def get_preferences() -> UIPreferencesResponse:
    """Return the founder's stored UI preferences (DB is source of truth)."""
    prefs = get_engine().get_ui_preferences()
    return UIPreferencesResponse(**prefs.model_dump(mode="json"))


@app.patch("/preferences", response_model=UIPreferencesResponse)
def patch_preferences(req: UIPreferencesUpdateRequest) -> UIPreferencesResponse:
    """Patch one or more UI preferences. 422 on an invalid ``reduce_motion``."""
    try:
        prefs = get_engine().set_ui_preferences(
            theme_id=req.theme_id,
            auto_enabled=req.auto_enabled,
            reduce_motion=req.reduce_motion,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return UIPreferencesResponse(**prefs.model_dump(mode="json"))


@app.get("/targets")
def get_targets() -> dict[str, Any]:
    """Return the founder / team_proposal / agreed targets trio.

    Used by the cyberpunk header (``ledger.js``) to render the
    ``rev: $X / $Y`` and ``days: N/M`` stats. Always returns a payload —
    the founder slot is populated even on a fresh install (all zeros).
    """
    bundle = get_engine().get_targets_bundle()
    return {
        "founder": bundle.founder.model_dump(mode="json"),
        "proposal": (
            bundle.proposal.model_dump(mode="json")
            if bundle.proposal is not None
            else None
        ),
        "agreed": (
            bundle.agreed.model_dump(mode="json")
            if bundle.agreed is not None
            else None
        ),
        "review_thread_id": bundle.review_thread_id,
        # Convenience: the authoritative numbers downstream readers want
        # without picking the right key themselves.
        "authoritative": get_engine().get_targets().model_dump(mode="json"),
    }


@app.post("/targets/review")
def post_targets_review() -> dict[str, Any]:
    """Re-run the team feasibility review on demand.

    Creates a fresh ``approval_request(action_type='target_feasibility')``
    and returns its payload. Returns ``404`` if no founder targets are
    set yet — the founder must complete onboarding first.
    """
    payload = get_engine().run_target_feasibility_review()
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail="No founder targets set; complete onboarding first.",
        )
    return payload


class _ProposeFirstDirectivesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force_heuristic: bool = False
    # When true, wipe any existing drafts before proposing so the LLM
    # regenerates a fresh set. Triggered by the step-5 "regenerate
    # proposals" button.
    force: bool = False


@app.post("/onboarding/propose_first_directives")
def post_propose_first_directives(
    req: _ProposeFirstDirectivesRequest | None = None,
) -> dict[str, Any]:
    """Team-proposes-first-directives — implements the contract in
    ``docs/context/operations.md:60-62`` for the first-move wizard step.

    Reads ``targets.agreed`` + company state, runs a short CEO pass,
    writes up to 3 draft projects, and returns a structured result so
    the UI can distinguish "team thought hard" from "team unreachable,
    here are generic seeds." Lying about the source of the directives
    erodes the whole product story.

    Response shape::

        {
            "status": "ok" | "team_failed" | "no_targets" | "heuristic",
            "directives": [...],
            "error_code": str|None,
            "error_message": str|None,
            "provider": str|None
        }

    Pass ``force_heuristic=true`` to explicitly accept the local
    fallback after the founder sees an error ("use built-in starter
    pack" path).

    Idempotent on existing drafts: when drafts already exist
    (template-staged or from a prior call), returns them without
    spending another LLM call.
    """
    engine = get_engine()
    force_h = bool(req and req.force_heuristic)
    force_regen = bool(req and req.force)
    return engine.propose_first_directives(
        force_heuristic=force_h, force=force_regen
    )


class _DiscussFirstDirectivesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)


@app.post("/onboarding/discuss_first_directives")
def post_discuss_first_directives(
    req: _DiscussFirstDirectivesRequest,
) -> dict[str, Any]:
    """Founder Q&A on the current first-week directives.

    Runs ONE CEO LLM call that takes (a) the founder's question and
    (b) the current draft directives; returns the CEO's answer and
    optionally a revised directive list. The frontend stacks each
    Q&A pair below the cards; when ``directives_changed=true`` it
    re-renders the cards in place.
    """
    engine = get_engine()
    return engine.discuss_first_directives(req.question)


# ---------------------------------------------------------------------------
# Company glossary (glossary-and-drift-detection task 05-19)
# ---------------------------------------------------------------------------


class GlossaryWriteRequest(BaseModel):
    """Body for ``POST /glossary`` / ``PATCH /glossary/<term>``."""

    model_config = ConfigDict(extra="forbid")

    term: str | None = Field(default=None, min_length=1)
    definition: str | None = Field(default=None, min_length=1)
    forbidden_synonyms: list[str] | None = None


@app.get("/glossary")
def list_glossary() -> list[dict[str, Any]]:
    """Return every glossary entry."""
    return get_engine().list_glossary()


@app.get("/glossary/{term}")
def show_glossary_term(term: str) -> dict[str, Any]:
    """Look up one term (case-insensitive). Returns 404 when missing."""
    entry = get_engine().get_glossary_term(term)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Term not found: {term!r}")
    return entry


@app.post("/glossary")
def create_glossary_term(req: GlossaryWriteRequest) -> dict[str, Any]:
    """Insert a brand-new glossary term (founder-sourced)."""
    if not req.term or not req.definition:
        raise HTTPException(
            status_code=422,
            detail="term and definition are required to create a glossary entry",
        )
    try:
        return get_engine().add_glossary_term(
            term=req.term,
            definition=req.definition,
            forbidden_synonyms=req.forbidden_synonyms,
            added_by="founder",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/glossary/{term}")
def patch_glossary_term(term: str, req: GlossaryWriteRequest) -> dict[str, Any]:
    """Update an existing glossary term's definition or forbidden synonyms."""
    try:
        return get_engine().update_glossary_term(
            term=term,
            definition=req.definition,
            forbidden_synonyms=req.forbidden_synonyms,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/glossary/{term}")
def delete_glossary_term(term: str) -> dict[str, Any]:
    """Drop a glossary term. Returns ``{"removed": bool}``."""
    removed = get_engine().remove_glossary_term(term)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Term not found: {term!r}")
    return {"removed": True, "term": term}


@app.get("/audit/recent")
def audit_recent(limit: int = 40) -> list[dict[str, Any]]:
    """Return the most recent audit events, oldest-first, for the live
    timeline to BACKFILL on dashboard load.

    SSE has no replay, so events fired before the browser's EventSource
    connects (e.g. a kickoff that ran while the founder was still on the
    onboarding screen) are otherwise invisible. This lets the timeline
    show what already happened, then continue live from SSE.
    """
    engine = get_engine()
    rows = engine.audit.recent(limit=max(1, min(int(limit), 200)))
    # ``recent`` returns newest-first; reverse so the timeline appends in
    # chronological order.
    out: list[dict[str, Any]] = []
    for row in reversed(rows):
        out.append({
            "event_type": row.get("event_type"),
            "action": row.get("action"),
            "agent_role": row.get("agent_role"),
            "project_id": row.get("project_id"),
            "created_at": row.get("created_at") or row.get("timestamp"),
        })
    return out


@app.get("/episodes")
def list_episodes(retention: str | None = None) -> list[dict[str, Any]]:
    """List materialized project episodes."""
    engine = get_engine()
    return engine.list_episodes(retention_tier=retention)


@app.get("/episodes/{project_id}")
def get_episode(project_id: str) -> dict[str, Any]:
    """Fetch one episode by project id."""
    engine = get_engine()
    row = engine.get_episode(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Episode not found: {project_id}")
    return row


@app.post("/episodes/{project_id}/rebuild")
def rebuild_episode(project_id: str) -> dict[str, Any]:
    """Force re-materialization of a project's episode."""
    engine = get_engine()
    try:
        return engine.rebuild_episode(project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class DistillationRequest(BaseModel):
    since: str | None = None
    dry_run: bool = False
    episode_ids: list[str] | None = None


@app.post("/distillation/run")
def run_distillation(req: DistillationRequest | None = None) -> dict[str, Any]:
    """Run CoS cross-episode distillation.

    Body fields (all optional):

    * ``since`` — human window like ``"30d"`` / ``"12h"``; defaults to 30d.
    * ``dry_run`` — when true the LLM runs but no memories are written.
    * ``episode_ids`` — explicit subset that bypasses both ``since`` and
      the 50-episode cap.
    """
    from datetime import timedelta

    body = req or DistillationRequest()
    window: timedelta | None = None
    if body.since is not None:
        text = body.since.strip().lower()
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        try:
            if text and text[-1] in units:
                window = timedelta(seconds=float(text[:-1]) * units[text[-1]])
            elif text:
                window = timedelta(seconds=float(text))
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid 'since' value: {body.since!r}",
            ) from exc

    try:
        return get_engine().distill(
            since=window,
            dry_run=body.dry_run,
            episode_ids=body.episode_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/memories/{agent_role}")
def list_memories(
    agent_role: str,
    limit: int = 20,
    include_stale: bool = False,
    knowledge_type: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """List memories for an agent."""
    engine = get_engine()
    return engine.list_memories(
        agent_role,
        limit=limit,
        include_stale=include_stale,
        knowledge_type=knowledge_type,
        category=category,
    )


@app.post("/remote/command")
def remote_command(req: RemoteCommandAPIRequest) -> dict[str, Any]:
    """Handle an authenticated inbound remote command."""
    engine = get_engine()
    return engine.handle_remote_command(req.model_dump())


@app.post("/remote/telegram")
def remote_telegram(update: dict[str, Any]) -> dict[str, Any]:
    """Handle a Telegram webhook-style update."""
    engine = get_engine()
    return engine.handle_remote_command(request_from_telegram_update(update))


@app.post("/remote/replays/cleanup")
def cleanup_remote_replays(req: RemoteReplayCleanupRequest | None = None) -> dict[str, Any]:
    """Delete expired remote replay records."""
    engine = get_engine()
    req = req or RemoteReplayCleanupRequest()
    return engine.cleanup_remote_replays(ttl_seconds=req.ttl_seconds)


@app.get("/observability")
def observability() -> dict[str, Any]:
    """Return an operational observability/RPG snapshot."""
    engine = get_engine()
    return engine.observability_snapshot()


@app.get("/dashboard", response_class=HTMLResponse)
def web_dashboard(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str = "",
) -> HTMLResponse:
    engine = get_engine()
    auth_error = _dashboard_auth_error(engine.settings, authorization, token, request)
    if auth_error is not None:
        if auth_error.status_code == 401 and not authorization and not token:
            return HTMLResponse(_render_dashboard_login(), status_code=401)
        raise auth_error
    return HTMLResponse(render_dashboard(engine.observability_snapshot()))


@app.get("/dashboard/login", response_class=HTMLResponse)
def dashboard_login() -> HTMLResponse:
    engine = get_engine()
    if not getattr(engine.settings, "web_dashboard_token", ""):
        raise HTTPException(status_code=503, detail="web dashboard auth is not configured")
    return HTMLResponse(_render_dashboard_login())


@app.post("/dashboard/login")
def dashboard_login_submit(
    request: Request,
    dashboard_token: str = Form(...),
) -> RedirectResponse:
    engine = get_engine()
    expected = getattr(engine.settings, "web_dashboard_token", "")
    if not expected:
        raise HTTPException(status_code=503, detail="web dashboard auth is not configured")
    if not compare_digest(dashboard_token, expected):
        raise HTTPException(status_code=401, detail="invalid dashboard token")

    response = RedirectResponse("/dashboard", status_code=303)
    ttl = getattr(engine.settings, "dashboard_session_ttl_seconds", 12 * 60 * 60)
    response.set_cookie(
        "kompany_dashboard_session",
        _dashboard_session_value(expected),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=ttl,
    )
    return response


@app.get("/dashboard/logout")
def dashboard_logout() -> RedirectResponse:
    response = RedirectResponse("/dashboard/login", status_code=303)
    response.delete_cookie(
        "kompany_dashboard_session",
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/dashboard/action")
def dashboard_action(
    req: DashboardActionRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    token: str = "",
) -> dict[str, Any]:
    engine = get_engine()
    auth_error = _dashboard_auth_error(engine.settings, authorization, token, request)
    if auth_error is not None:
        raise auth_error
    action = req.action.strip().lower().replace("_", "-")
    if action == "runtime-status":
        result = engine.get_runtime_state()
    elif action == "heartbeat":
        result = engine.heartbeat_once()
    elif action == "approvals":
        result = {"approvals": engine.list_approvals()}
    elif action == "replay-cleanup":
        result = engine.cleanup_remote_replays()
    elif action == "approve":
        if not req.approval_id:
            raise HTTPException(status_code=400, detail="approval_id is required")
        approval = engine.approve_request(req.approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="approval not found")
        result = {"approval": approval}
    elif action == "reject":
        if not req.approval_id:
            raise HTTPException(status_code=400, detail="approval_id is required")
        approval = engine.reject_request(req.approval_id, reason=req.reason or "dashboard rejection")
        if approval is None:
            raise HTTPException(status_code=404, detail="approval not found")
        result = {"approval": approval}
    elif action == "runtime-suspend":
        result = engine.suspend(reason=req.reason or "dashboard request")
    elif action == "runtime-resume":
        result = engine.resume()
    else:
        raise HTTPException(status_code=400, detail="unsupported dashboard action")
    return {"status": "ok", "action": action, "result": result}


def _dashboard_auth_error(
    settings: Any,
    authorization: str | None,
    token: str,
    request: Request | None = None,
) -> HTTPException | None:
    expected = getattr(settings, "web_dashboard_token", "")
    if not expected:
        return HTTPException(status_code=503, detail="web dashboard auth is not configured")

    supplied = token
    if not supplied and authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            supplied = value

    if supplied and compare_digest(supplied, expected):
        return None
    if request is not None:
        session = request.cookies.get("kompany_dashboard_session", "")
        ttl = getattr(settings, "dashboard_session_ttl_seconds", 12 * 60 * 60)
        if session and _dashboard_session_valid(expected, session, ttl):
            return None
    return HTTPException(status_code=401, detail="invalid dashboard token")


def _dashboard_session_value(token: str) -> str:
    issued_at = str(int(time.time()))
    return f"{issued_at}.{_dashboard_session_signature(token, issued_at)}"


def _dashboard_session_valid(token: str, session: str, ttl_seconds: int) -> bool:
    issued_at, separator, signature = session.partition(".")
    if not separator or not issued_at.isdigit():
        return False
    if int(issued_at) + max(0, ttl_seconds) < int(time.time()):
        return False
    expected = _dashboard_session_signature(token, issued_at)
    return compare_digest(signature, expected)


def _dashboard_session_signature(token: str, issued_at: str) -> str:
    return hmac.new(
        token.encode("utf-8"),
        f"kompany-dashboard-session:{issued_at}".encode("utf-8"),
        "sha256",
    ).hexdigest()


def _render_dashboard_login() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kompany Dashboard Login</title>
  <style>
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif; background: radial-gradient(circle at top left, #26345f, #10131a 46%, #080a10); color: #f6f7fb; }
    main { width: min(440px, calc(100vw - 32px)); background: rgba(24, 29, 41, .9); border: 1px solid #2a3347; border-radius: 22px; padding: 28px; box-shadow: 0 20px 60px rgba(0, 0, 0, .3); }
    .label { color: #a8b3cf; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    h1 { margin: 8px 0 12px; }
    p { color: #c7d2ef; }
    label { display: grid; gap: 8px; margin: 20px 0; }
    input { width: 100%; border: 1px solid #3a4b6d; border-radius: 12px; padding: 12px; background: #10131a; color: #f6f7fb; font: inherit; }
    button { width: 100%; border: 0; border-radius: 12px; padding: 12px; background: linear-gradient(135deg, #78ffd6, #80d8ff); color: #10131a; font-weight: 800; cursor: pointer; }
  </style>
</head>
<body>
  <main>
    <div class="label">Kompany Dashboard</div>
    <h1>Enter dashboard token</h1>
    <p>Use the configured dashboard token to start a private browser session.</p>
    <form method="post" action="/dashboard/login">
      <label>
        Dashboard token
        <input name="dashboard_token" type="password" autocomplete="current-password" autofocus required>
      </label>
      <button type="submit">Open dashboard</button>
    </form>
  </main>
</body>
</html>"""


@app.get("/runtime")
def runtime_status() -> dict[str, Any]:
    """Return engine runtime state."""
    engine = get_engine()
    return engine.get_runtime_state()


@app.post("/heartbeat")
def heartbeat(req: HeartbeatRequest | None = None) -> dict[str, Any]:
    """Run one heartbeat check."""
    engine = get_engine()
    req = req or HeartbeatRequest()
    return engine.heartbeat_once(dispatch=req.dispatch, adapter=req.adapter)


@app.post("/notifications/dispatch")
def dispatch_notifications(req: DispatchNotificationsRequest) -> list[dict[str, Any]]:
    """Dispatch notification events."""
    engine = get_engine()
    return engine.dispatch_notifications(req.events, adapter=req.adapter)


class SuspendRequest(BaseModel):
    reason: str = "manual"


class ToolProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str = Field(..., min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    reason: str | None = None
    project_id: str | None = None
    task_id: str | None = None


@app.get("/tools")
def list_tools_registry() -> list[dict[str, Any]]:
    """Registered tools with side_effect / autonomy tier / connection state."""
    engine = get_engine()
    return engine.tools_list()


@app.post("/tools/propose")
def propose_tool_action(req: ToolProposeRequest) -> dict[str, Any]:
    """Propose a tool action (#4) — files a tool_action approval card.

    Nothing executes now; approving the card (POST /approvals/{id}/approve)
    runs the action for real. PAID actions can ONLY run through this path."""
    engine = get_engine()
    try:
        return engine.propose_action(
            req.tool_name,
            req.inputs,
            summary=req.summary or f"Run {req.tool_name}",
            reason=req.reason,
            project_id=req.project_id,
            task_id=req.task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/tools/policies")
def list_tool_policies(agent_role: str | None = None) -> list[dict[str, Any]]:
    """List tool authorization policies."""
    engine = get_engine()
    return engine.list_tool_policies(agent_role=agent_role)


@app.post("/tools/policies")
def set_tool_policy(req: ToolPolicyRequest) -> dict[str, Any]:
    """Create or update a tool authorization policy."""
    engine = get_engine()
    return engine.set_tool_policy(
        req.agent_role,
        req.tool_name,
        req.allowed,
        reason=req.reason,
        requires_approval=req.requires_approval,
    )


@app.post("/tools/authorize")
def authorize_tool(req: ToolAuthorizationRequest) -> dict[str, Any]:
    """Check whether an agent may use a tool."""
    engine = get_engine()
    return engine.authorize_tool(req.agent_role, req.tool_name, purpose=req.purpose)


@app.post("/tools/use")
def use_tool(req: ToolAuthorizationRequest) -> dict[str, Any]:
    """Authorize a tool use through the engine gate."""
    engine = get_engine()
    return engine.use_tool(
        req.agent_role,
        req.tool_name,
        purpose=req.purpose,
        arguments=req.arguments,
        approval_id=req.approval_id,
    )


@app.post("/runtime/suspend")
def runtime_suspend(req: SuspendRequest) -> dict[str, Any]:
    """Suspend the engine."""
    engine = get_engine()
    return engine.suspend(reason=req.reason)


@app.post("/runtime/resume")
def runtime_resume() -> dict[str, Any]:
    """Resume the engine."""
    engine = get_engine()
    return engine.resume()


class CreateBackupRequest(BaseModel):
    label: str = "manual"


@app.post("/backups")
def create_backup(req: CreateBackupRequest) -> dict[str, Any]:
    """Create a labeled SQLite snapshot."""
    engine = get_engine()
    return engine.create_backup(label=req.label)


@app.get("/backups")
def list_backups() -> list[dict[str, Any]]:
    """List SQLite snapshots, newest first."""
    engine = get_engine()
    return engine.list_backups()


@app.post("/backups/{backup_id}/restore")
def restore_backup(backup_id: str) -> dict[str, Any]:
    """Restore a SQLite snapshot."""
    engine = get_engine()
    try:
        return engine.restore_backup(backup_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/credentials")
def list_credentials() -> list[dict[str, Any]]:
    engine = get_engine()
    return engine.list_credentials()


@app.post("/credentials")
def set_credential(req: CredentialRequest) -> dict[str, Any]:
    engine = get_engine()
    return engine.set_credential(req.name, req.value)


@app.delete("/credentials/{name}")
def delete_credential(name: str) -> dict[str, Any]:
    engine = get_engine()
    return engine.delete_credential(name)


@app.post("/credentials/rotate-key")
def rotate_credential_key(req: CredentialKeyRotationRequest) -> dict[str, Any]:
    engine = get_engine()
    return engine.rotate_credential_key(req.new_vault_key)


@app.get("/status")
def get_status() -> dict[str, Any]:
    """Get company status (canonical dict — see core/status_ops.py)."""
    from kompany.core.status_ops import build_status

    return build_status(get_engine())


@app.post("/debate")
def run_debate(req: DebateRequest) -> dict[str, Any]:
    """Run a full multi-agent debate on a strategic question."""
    from kompany.core.debate import DebateEngine

    engine = get_engine()
    stage = engine.settings.company_stage or "solo"
    debate_engine = DebateEngine(engine.registry, stage=stage)
    result = debate_engine.run(
        question=req.question,
        company_state=engine.get_company_state(),
    )
    return {
        "question": result.question,
        "rounds": [[pos.model_dump() for pos in rnd] for rnd in result.rounds],
        "synthesis": result.synthesis.model_dump() if result.synthesis else None,
        "decision": result.decision.model_dump() if result.decision else None,
    }


@app.get("/projects")
def list_projects(include_draft: bool = False) -> list[dict[str, Any]]:
    """List projects.

    By default returns only ``status='active'`` rows (legacy behaviour
    used by the dashboard timeline + inbox).  Pass ``?include_draft=1``
    to additionally include rows ``Templates.apply`` staged as drafts —
    the onboard-v2 First Move step (PRD 05-19-onboard-v2-flow) reads
    those to render its three suggested-directive cards.

    Draft rows use a literal ``'draft'`` status string that's outside
    :class:`ProjectStatus`, so we serialise the raw value instead of
    routing through ``Projects.list_active``.
    """
    engine = get_engine()
    if not include_draft:
        active = engine.projects.list_active()
        return [
            {
                "id": p.id,
                "name": p.name,
                "type": p.type.value,
                "status": p.status.value,
                "target_amount": p.target_amount,
                "funded_amount": p.funded_amount,
            }
            for p in active
        ]
    # Raw scan including drafts. We can't push these through
    # ``_row_to_project`` because that constructor coerces status into
    # the enum (no DRAFT member). Read the SQL row directly.
    rows = engine.db.execute(
        "SELECT id, name, type, status, target_amount, funded_amount "
        "FROM projects "
        "WHERE status IN ('active', 'draft') "
        "ORDER BY created_at DESC"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "type": r["type"],
            "status": r["status"],
            "target_amount": r["target_amount"],
            "funded_amount": r["funded_amount"],
        }
        for r in rows
    ]


@app.get("/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    """Get a specific project by ID."""
    engine = get_engine()
    p = engine.projects.get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    tasks = engine.projects.list_tasks(p.id)
    return {
        "id": p.id,
        "name": p.name,
        "type": p.type.value,
        "status": p.status.value,
        "target_amount": p.target_amount,
        "funded_amount": p.funded_amount,
        "plan": p.plan,
        "assigned_agents": p.assigned_agents,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "agent": t.assigned_agent,
                "status": t.status.value,
            }
            for t in tasks
        ],
    }


@app.get("/ledger")
def get_ledger(limit: int = 10) -> list[dict]:
    """Get recent ledger entries."""
    engine = get_engine()
    return engine.ledger.get_recent(limit=limit)


@app.get("/approvals")
def list_approvals(status: str | None = None) -> list[dict[str, Any]]:
    """List approval requests. ``status`` query param filters by state;
    omit it to get only pending rows (legacy default)."""
    engine = get_engine()
    if status is None:
        return engine.list_approvals()
    return [r.model_dump(mode="json") for r in engine.approvals.list_by_status(status=status)]


@app.get("/approvals/{approval_id}")
def show_approval(approval_id: str) -> dict[str, Any]:
    """Return one approval with its thread + comment timeline."""
    engine = get_engine()
    data = engine.get_approval(approval_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return data


@app.get("/inbox")
def inbox() -> list[dict[str, Any]]:
    """RPG inbox: pending + snoozed approvals with comment counts."""
    return get_engine().inbox()


@app.post("/approvals/{approval_id}/approve")
def approve_request(
    approval_id: str,
    req: ApproveApprovalRequest | None = None,
) -> dict[str, Any]:
    """Approve a pending request."""
    engine = get_engine()
    comment = req.comment if req and req.comment else None
    result = engine.approve_request(approval_id, comment_body=comment)
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/approvals/{approval_id}/reject")
def reject_request(approval_id: str, req: RejectApprovalRequest) -> dict[str, Any]:
    """Reject a pending request."""
    engine = get_engine()
    result = engine.reject_request(
        approval_id,
        reason=req.reason,
        comment_body=req.comment or None,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/approvals/{approval_id}/revise")
def revise_request(approval_id: str, req: ReviseApprovalRequest) -> dict[str, Any]:
    """Counter-propose: original -> ``revision_requested``, new pending row spawned."""
    engine = get_engine()
    result = engine.request_approval_revision(
        approval_id,
        counter=req.counter,
        comment_body=req.comment or None,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/approvals/{approval_id}/snooze")
def snooze_request(approval_id: str, req: SnoozeApprovalRequest) -> dict[str, Any]:
    """Snooze an approval; watchdog auto-unsnoozes when due."""
    engine = get_engine()
    result = engine.snooze_approval(
        approval_id,
        minutes=req.minutes,
        comment_body=req.comment or None,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/approvals/{approval_id}/cancel")
def cancel_request(approval_id: str, req: CancelApprovalRequest) -> dict[str, Any]:
    """Cancel an approval (terminal)."""
    engine = get_engine()
    result = engine.cancel_approval(
        approval_id,
        reason=req.reason or None,
        comment_body=req.comment or None,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/approvals/{approval_id}/comment")
def comment_request(approval_id: str, req: CommentApprovalRequest) -> dict[str, Any]:
    """Append a free-form comment to an approval thread."""
    engine = get_engine()
    result = engine.comment_on_approval(
        approval_id,
        body=req.body,
        by_type=req.by_type,
        by_id=req.by_id,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
    return result


@app.post("/projects/{project_id}/execute")
def execute_project(project_id: str) -> dict[str, Any]:
    """Execute a revenue project's tasks autonomously."""
    engine = get_engine()
    p = engine.projects.get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return engine.execute_project(project_id)


def _kickoff_project_safely(project_id: str) -> None:
    """Background-task wrapper around ``engine.execute_project`` used by
    the First-Move activation handler.

    A founder finishing onboarding picked a directive and expected the
    team to actually START WORKING. Just flipping ``status='active'``
    leaves the dashboard idle until something else triggers a run —
    historically the founder had to call ``/projects/{id}/execute``
    manually, which they have no way to discover. Bug surfaced
    2026-05-26 when a freshly-onboarded user landed on the dashboard
    with cash $50, 1 active project, and 11 idle agents — staring at
    an empty office wondering "what now?".

    Any exception inside this background task is swallowed + audited
    so a runner blow-up never poisons the HTTP response the founder
    is waiting on. The next dashboard refresh will surface the audit
    entry in the live timeline.
    """
    engine = get_engine()
    try:
        engine.execute_project(project_id)
    except Exception as exc:  # noqa: BLE001 — background swallow
        engine.audit.record(
            event_type="project.kickoff_failed",
            action=f"Background kickoff for project {project_id} failed",
            detail={"project_id": project_id, "error": str(exc)},
            project_id=project_id,
        )


class CancelProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = ""


@app.post("/projects/{project_id}/abandon")
@app.post("/projects/{project_id}/cancel")
def cancel_project(
    project_id: str, req: CancelProjectRequest | None = None
) -> dict[str, Any]:
    """Abandon a plan (#10): project → ``cancelled``, unfinished tasks
    stopped, open approval cards withdrawn, unspent envelope released
    back to the treasury (earmark accounting — no ledger row needed).
    AI cost already spent is real and stays in the ledger. Idempotent
    on an already-terminal project. ``/cancel`` is a legacy alias of
    ``/abandon`` — same handler, same engine op.
    """
    engine = get_engine()
    reason = (req.reason if req else "") or ""
    try:
        return engine.abandon_project(project_id, reason=reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projects/{project_id}/activate")
def activate_project(
    project_id: str, background_tasks: BackgroundTasks = None  # type: ignore[assignment]
) -> dict[str, Any]:
    """Promote a ``status='draft'`` project to ``active``.

    Used by the onboard-v2 "First Move" step (PRD 05-19-onboard-v2-flow):
    after ``apply_template`` stages suggested directives as draft
    projects, the founder picks one and we flip its status so the engine
    will pick it up on the next directive sweep. The two unselected
    drafts stay in ``draft`` for later activation.

    Returns ``404`` when no project matches the id. Idempotent: an
    already-active project is returned unchanged.
    """
    engine = get_engine()
    # Touch the raw row first so we can distinguish "not found" from the
    # ``status != 'draft'`` branch ``Projects.get`` would happily return.
    row = engine.db.execute(
        "SELECT id, status FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    current_status = row["status"]
    if current_status == "active":
        # Idempotent — caller can replay this safely (e.g. retry after a
        # network blip during First Move submit).
        proj = engine.projects.get(project_id)
        return {
            "id": project_id,
            "status": "active",
            "previous_status": "active",
            "name": proj.name if proj else None,
        }
    # Flip the literal status string (``ProjectStatus`` has no DRAFT
    # member; ``Templates.apply`` writes ``'draft'`` directly).
    engine.db.execute(
        "UPDATE projects SET status = 'active', updated_at = datetime('now') "
        "WHERE id = ?",
        (project_id,),
    )
    engine.db.commit()
    engine.audit.record(
        event_type="project.activated",
        action=f"Activated project from {current_status} to active",
        detail={
            "project_id": project_id,
            "previous_status": current_status,
            "source": "onboarding.first_move",
        },
        project_id=project_id,
    )
    # Auto-kickoff: schedule the actual run in the background so the
    # founder doesn't land on an idle dashboard. The HTTP response
    # returns fast (sub-50ms) and the team starts working in parallel.
    # ``background_tasks`` is ``None`` for direct in-process calls
    # (tests, CLI) — skip the kickoff in that case so test fixtures
    # don't unexpectedly burn LLM tokens.
    kickoff_scheduled = False
    if background_tasks is not None:
        background_tasks.add_task(_kickoff_project_safely, project_id)
        engine.audit.record(
            event_type="project.kickoff_scheduled",
            action=f"Scheduled background kickoff for project {project_id}",
            detail={"project_id": project_id},
            project_id=project_id,
        )
        kickoff_scheduled = True
    proj = engine.projects.get(project_id)
    return {
        "id": project_id,
        "status": "active",
        "previous_status": current_status,
        "name": proj.name if proj else None,
        "kickoff_scheduled": kickoff_scheduled,
    }


# ---------------------------------------------------------------------------
# LLM spend summary (onboard-v2 cost chip + dashboard cost chip)
# ---------------------------------------------------------------------------


@app.get("/llm/spend/summary")
def llm_spend_summary() -> dict[str, Any]:
    """Aggregate AI_COST ledger rows for the dashboard LLM spend chip.

    The PREVIEW / STREAM / LEDGER discipline (memory:
    [[engineering-cost-visibility-discipline]]) requires every LLM call
    to land an ``AI_COST`` ledger row. This endpoint sums them so the
    cyberpunk header can render a running total without subscribing to
    SSE just to keep a counter. The chip refreshes on every ``llm.spend``
    SSE envelope (incremental) and reconciles against this endpoint on
    page load / focus-restore (authoritative).
    """
    engine = get_engine()
    try:
        totals = engine.ledger.get_totals()
    except Exception:
        return {"total_usd": 0.0, "row_count": 0}
    # ledger amount for AI_COST rows is stored as a negative number
    # (it's an expense). The chip wants the magnitude.
    raw = float(totals.get("ai_cost", 0.0))
    total_usd = abs(raw)
    row = engine.db.execute(
        "SELECT COUNT(*) AS c FROM ledger WHERE category = 'ai_cost'"
    ).fetchone()
    row_count = int(row["c"]) if row else 0
    return {"total_usd": total_usd, "row_count": row_count}


# ---------------------------------------------------------------------------
# Web UI: SSE feed + static file serving
# ---------------------------------------------------------------------------

# The 11 canonical C-suite roles, in display order. Used as the source of
# truth for the office panel — the UI always shows 11 rows whether or not
# audit_log mentions a role.
C_SUITE_ROLES: tuple[str, ...] = (
    "ceo", "cfo", "cto", "cpo", "cmo", "cro",
    "coo", "csa", "ciso", "cos", "cv",
)

# Heartbeat keepalive interval (seconds). EventSource drops if the server
# stays silent for too long behind some proxies — 15s is the conservative
# default used by SSE bridges in the wild.
_SSE_HEARTBEAT_SECONDS = 15.0


async def _sse_event_stream() -> AsyncIterator[bytes]:
    """Async generator that yields SSE-formatted bytes for one client.

    Multiplexes:
      * an :class:`asyncio.Queue` fed by the global :class:`EventHub`
      * a periodic ``:keepalive`` comment so reverse proxies don't time out
    """
    hub = get_event_hub()
    # Capture the loop this SSE stream runs on so publishes from
    # background threads (the kickoff task) can hop back onto it via
    # call_soon_threadsafe instead of being dropped.
    hub.register_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
    hub._subscribers.add(queue)  # type: ignore[attr-defined]
    try:
        # Tell the client we're alive immediately. Some browsers wait for
        # the first byte before resolving the EventSource ``open`` event.
        yield b": connected\n\n"
        while True:
            try:
                envelope = await asyncio.wait_for(
                    queue.get(),
                    timeout=_SSE_HEARTBEAT_SECONDS,
                )
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
                continue
            event_type = envelope.get("type", "message")
            event_id = envelope.get("id", 0)
            # Wrap the type into the JSON payload and use the *default*
            # ``message`` event so a single ``onmessage`` listener can
            # demultiplex every audit.<x> / inbox.updated / health.event
            # flavor. EventSource cannot wildcard named events, and we
            # don't want to enumerate every possible ``audit.<subtype>``.
            envelope_out = {
                "type": event_type,
                "data": dict(envelope.get("data", {}) or {}),
            }
            data = json.dumps(envelope_out, default=str)
            payload = (
                f"id: {event_id}\n"
                f"data: {data}\n\n"
            ).encode("utf-8")
            yield payload
    finally:
        hub._subscribers.discard(queue)  # type: ignore[attr-defined]


@app.get("/events")
async def events_stream() -> StreamingResponse:
    """Server-Sent Events feed of live engine activity.

    Browser ``EventSource`` clients connect here to receive ``audit.*``,
    ``inbox.updated``, ``health.event``, and ``episode.recorded`` events
    in real time. No history is replayed — only events emitted after the
    client connects.
    """
    return StreamingResponse(
        _sse_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Disable nginx response buffering — SSE depends on chunks
            # arriving as soon as the server flushes them.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/anima/state")
def anima_state() -> dict[str, Any]:
    """Anima persona state (06-12-anima-persona PRD D5): valence,
    energy, derived tone, last_diary_date."""
    return get_engine().anima_state()


@app.get("/anima/diary")
def anima_diary(limit: int = 30) -> list[dict[str, Any]]:
    """Recent Anima diary entries, newest first."""
    return get_engine().anima_diary_list(limit=limit)


@app.get("/channels/status")
def channels_status() -> dict[str, Any]:
    """Channel adapter health + outbox counts (06-12-channels PRD D5)."""
    return get_engine().channels_status()


@app.get("/channels/outbox")
def channels_outbox(limit: int = 20) -> list[dict[str, Any]]:
    """Most recent channel outbox rows, newest first."""
    return get_engine().outbox_list(limit=limit)


@app.get("/agents/work-summary")
def agents_work_summary() -> dict[str, dict[str, Any]]:
    """Per-agent task-history summary (06-12-panel-truthfulness #22).

    Keyed by lowercase role; lets the UI distinguish "worked but no
    episode closed yet" from "never worked"."""
    return get_engine().agent_work_summary()


@app.get("/agents/status")
def agents_status() -> list[dict[str, Any]]:
    """Return the live status of all 11 C-suite roles for the office panel.

    Source of truth: the ``agent_status`` table if present, with the most
    recent ``audit_log`` activity as a heuristic fallback when an agent
    has no explicit status row yet. The response always contains exactly
    11 rows in canonical display order.
    """
    engine = get_engine()
    rows_by_role: dict[str, dict[str, Any]] = {}

    # Primary source: agent_status store.
    try:
        for entry in engine.agent_status.list_all():  # type: ignore[attr-defined]
            role = (entry.get("agent_role") or entry.get("role") or "").lower()
            if role:
                rows_by_role[role] = entry
    except Exception:
        pass

    # Fallback: tail audit_log for any agent activity in the last ~5 min.
    if not rows_by_role:
        try:
            recent = engine.audit.recent(limit=200)
        except Exception:
            recent = []
        for row in recent:
            role = (row.get("agent_role") or "").lower()
            if not role or role in rows_by_role:
                continue
            rows_by_role[role] = {
                "agent_role": role,
                "status": "busy",
                "last_action": row.get("action"),
                "updated_at": row.get("timestamp"),
            }

    result = []
    from kompany.state.activity import derive_activity_kind

    for role in C_SUITE_ROLES:
        row = rows_by_role.get(role)
        if row is None:
            result.append({
                "role": role.upper(),
                "status": "idle",
                "last_action": None,
                "latency_ms": None,
                "activity_kind": "idle",
                "project_id": None,
                "project_type": None,
            })
        else:
            status = row.get("status") or "idle"
            project_type = row.get("project_type")
            result.append({
                "role": role.upper(),
                "status": status,
                "last_action": row.get("last_action") or row.get("action"),
                "latency_ms": row.get("latency_ms"),
                # Advisory kind: prefer the stored value; derive for audit-fallback
                # rows that predate the column.
                "activity_kind": row.get("activity_kind")
                or derive_activity_kind(role, status, project_type),
                "project_id": row.get("project_id"),
                "project_type": project_type,
            })
    return result


def _web_ui_dir() -> Path:
    """Resolve the bundled ``web_ui/`` directory inside the installed package."""
    return Path(__file__).resolve().parent.parent / "web_ui"


# Clean URL alias for the onboarding page so callers can link to
# ``/ui/onboarding`` instead of the bare ``.html`` file. Must be
# registered before the StaticFiles mount so it wins over the static
# router. Returns a 307 so the WebView updates its location bar to the
# canonical static path (preserves relative asset resolution).
@app.get("/ui/onboarding", include_in_schema=False)
def onboarding_alias() -> RedirectResponse:
    return RedirectResponse(url="/ui/onboarding.html", status_code=307)


# Mount the cyberpunk SPA at /ui. ``html=True`` tells StaticFiles to serve
# ``index.html`` for the directory root, so ``/ui/`` works without a list view.
_WEB_UI_DIR = _web_ui_dir()
if _WEB_UI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_WEB_UI_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Redirect bare ``/`` to the web UI."""
    return RedirectResponse(url="/ui/", status_code=307)
