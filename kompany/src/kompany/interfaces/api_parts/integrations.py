"""Integration listing, connect, propose and test endpoints.

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


@router.get("/integrations")
def list_integrations() -> list[dict[str, Any]]:
    """List registered integrations (loader-driven: builtins + plugins)
    with required credentials + whether the founder has connected each
    (all required credentials present in the vault). Canonical shape:
    ``engine.integrations_list()`` — same on MCP/SDK/CLI (#8)."""
    return get_engine().integrations_list()


@router.get("/credential-broker/status")
def credential_broker_status() -> dict[str, Any]:
    """Return provider-neutral broker health without secret material."""
    return get_engine().credential_broker_status()


@router.post("/integrations/email/connect", response_model=IntegrationActionResponse)
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


@router.get("/integrations/{integration_id}/credentials")
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
        "telegram": (
            ("telegram_bot_token", True),
            ("telegram_allowed_chat_ids", False),
        ),
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


class ConnectTelegramRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Empty bot_token means "keep the saved token" — lets the founder
    # edit allowed_chat_ids alone without re-pasting the token.
    bot_token: str = ""
    allowed_chat_ids: str = Field(..., min_length=1)


class ConnectResendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Empty string means "keep the saved key" — lets the founder change
    # the From without re-pasting the key every time.
    api_key: str = ""
    resend_from: str = Field(..., min_length=1)


@router.post("/integrations/resend/connect", response_model=IntegrationActionResponse)
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


@router.post("/integrations/telegram/connect", response_model=IntegrationActionResponse)
def connect_telegram(req: ConnectTelegramRequest) -> IntegrationActionResponse:
    """Store + verify a Telegram bot token via ``getMe`` before saving.

    Verifies the token authenticates with Telegram (catches a pasted
    wrong token now, not at worker start). On success stores both
    ``telegram_bot_token`` and ``telegram_allowed_chat_ids`` in the
    encrypted vault. Empty ``bot_token`` means "keep the saved token"
    so the founder can edit the chat-id allowlist alone."""
    import urllib.error
    import urllib.request

    engine = get_engine()
    if not getattr(engine.settings, "vault_key", ""):
        return IntegrationActionResponse(ok=False, detail="vault key not configured")
    token = req.bot_token.strip()
    if not token:
        token = engine.credentials.get("telegram_bot_token") or ""
    if not token:
        return IntegrationActionResponse(ok=False, detail="bot token required")
    try:
        vr = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getMe",
            headers={"User-Agent": "Kompany/0.1 (+https://kompany.dev)"},
        )
        with urllib.request.urlopen(vr, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return IntegrationActionResponse(
            ok=False, detail=f"getMe failed: HTTP {e.code} — invalid token"
        )
    except Exception as exc:  # noqa: BLE001
        return IntegrationActionResponse(
            ok=False, detail=f"getMe failed: {type(exc).__name__}: {exc}"
        )
    if not payload.get("ok") or not payload.get("result", {}).get("username"):
        return IntegrationActionResponse(ok=False, detail="getMe returned no bot username")
    username = payload["result"]["username"]
    engine.credentials.set("telegram_bot_token", token)
    engine.credentials.set("telegram_allowed_chat_ids", req.allowed_chat_ids.strip())
    engine.audit.record("integration.connected", "Connected Telegram",
                        detail={"integration": "telegram", "bot": username})
    return IntegrationActionResponse(ok=True, detail=f"connected @{username}")


class ProposeEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)


@router.post("/integrations/email/propose")
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


@router.post("/integrations/email/test", response_model=IntegrationActionResponse)
def test_email(req: TestEmailRequest | None = Body(default=None)) -> IntegrationActionResponse:
    """Send a test email (proves real sending).

    Default recipient is the connected From address (self-test). The
    founder can override ``to`` to send to a different inbox — that
    separates "did the send succeed" from "does my From mailbox receive".
    """
    from kompany.integrations.email_smtp import SendEmailTool, SendEmailInput

    engine = get_engine()
    sender = (
        engine.credentials.get("resend_from")
        or engine.credentials.get("smtp_from")
        or engine.credentials.get("smtp_user")
    )
    if not sender:
        return IntegrationActionResponse(ok=False, detail="email not connected")
    to = (req.to.strip() if req and req.to else "") or sender
    # Full 1.1.0 service bundle (#43) — one builder for every plugin Tool call.
    from kompany.core.tool_actions import build_tool_context

    ctx = build_tool_context(engine)
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
