"""Onboarding status / env defaults / stash / complete surface.

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

router = APIRouter()

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


@router.get("/onboarding/env_defaults", response_model=EnvDefaultsResponse)
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


@router.post(
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


@router.get(
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
    """Resolve the data dir the sidecar should use, consistent with engine.

    Full chain (issue #15): KOMPANY_DATA_DIR env > active workspace from
    the registry > ~/.kompany default."""
    from kompany.config.workspaces import resolve_data_dir

    return resolve_data_dir()


@router.get("/onboarding/status", response_model=OnboardingStatusResponse)
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


@router.post(
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

