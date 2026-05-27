"""Email integration (generic SMTP) — the first real Tool (#5).

Lets the team actually SEND email instead of only drafting it. Works
with Gmail app-passwords or any SMTP provider. This is the autonomy
unlock: a 'send outreach' task can now resolve to truly COMPLETED
(real message sent) instead of the DELIVERED/'YOUR MOVE' stopgap.

Send is an EXTERNAL_ACTION at APPROVAL tier — it never fires without
founder approval (the action pipeline / the connect+send REST path
both gate it). Credentials live in the encrypted vault.
"""

from __future__ import annotations

import json
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

from pydantic import BaseModel

from kompany.plugins.contract import (
    AutonomyTier,
    CostEstimate,
    Integration,
    SideEffect,
    Tool,
    ToolContext,
)

REQUIRED_CREDENTIALS = ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from")
RESEND_CREDENTIALS = ("resend_api_key", "resend_from")
RESEND_API = "https://api.resend.com/emails"


class SendEmailInput(BaseModel):
    to: str
    subject: str
    body: str


class SendEmailOutput(BaseModel):
    sent: bool
    detail: str = ""
    to: str = ""


def _smtp_send(creds: dict[str, str], to: str, subject: str, body: str) -> str:
    """Send one email via SMTP. Returns a short detail string. Raises on
    failure. STARTTLS on the given port (587 typical); implicit SSL on 465."""
    host = creds.get("smtp_host", "").strip()
    port = int(creds.get("smtp_port", "587") or "587")
    user = creds.get("smtp_user", "").strip()
    password = creds.get("smtp_password", "") or ""
    sender = (creds.get("smtp_from", "") or user).strip()
    if not host or not user or not password:
        raise ValueError("SMTP not fully configured (need host/user/password)")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)
    return f"sent to {to} via {host}:{port}"


def _resend_send(api_key: str, sender: str, to: str, subject: str, body: str) -> str:
    """Send one email via the Resend API. Returns a detail string with
    the message id. Raises on failure."""
    if not api_key or not sender:
        raise ValueError("Resend not fully configured (need api_key + from)")
    payload = json.dumps({
        "from": sender, "to": [to], "subject": subject, "text": body,
    }).encode("utf-8")
    req = urllib.request.Request(
        RESEND_API, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Cloudflare in front of api.resend.com returns
            # ``error code: 1010`` for requests without a sane
            # User-Agent (treats them as bots). Without this header
            # every send 403'd with the cryptic 1010 — nothing to do
            # with the Resend API key/domain themselves.
            "User-Agent": "Kompany/0.1 (+https://kompany.dev)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        return f"sent to {to} via Resend (id {data.get('id', '?')})"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        # Surface the full error body — Resend's actual `message` field
        # tells the real cause (account restricted vs domain unverified
        # vs sandbox-only). Earlier truncation hid it behind "1010".
        raise RuntimeError(f"Resend {e.code}: {body}") from e


class SendEmailTool(Tool):
    name = "email.send"
    description = (
        "Send a real email to one recipient. Use for outreach, follow-ups, "
        "and delivery once the founder has connected an email account."
    )
    input_schema = SendEmailInput
    output_schema = SendEmailOutput
    side_effect = SideEffect.EXTERNAL_ACTION
    autonomy_tier = AutonomyTier.APPROVAL

    def estimate_cost(self, inputs: BaseModel) -> CostEstimate:
        # SMTP send is free; no LLM, no external charge.
        return CostEstimate(llm_usd=0.0, external_usd=0.0, confidence=1.0)

    def execute(self, inputs: BaseModel, ctx: ToolContext) -> BaseModel:
        # Provider-aware: prefer Resend (native API) when connected,
        # else fall back to raw SMTP. One tool, either backend — the
        # agent just calls "email.send".
        resend_key = ctx.credentials.get("resend_api_key") or ""
        try:
            if resend_key:
                sender = ctx.credentials.get("resend_from") or ""
                detail = _resend_send(resend_key, sender, inputs.to, inputs.subject, inputs.body)
            else:
                creds = {k: (ctx.credentials.get(k) or "") for k in REQUIRED_CREDENTIALS}
                detail = _smtp_send(creds, inputs.to, inputs.subject, inputs.body)
            return SendEmailOutput(sent=True, detail=detail, to=inputs.to)
        except Exception as exc:  # noqa: BLE001 — surface honestly
            return SendEmailOutput(sent=False, detail=f"{type(exc).__name__}: {exc}", to=inputs.to)


class EmailIntegration(Integration):
    integration_id = "email_smtp"
    display_name = "Email (SMTP)"
    required_credentials = REQUIRED_CREDENTIALS

    def tools(self) -> list[Tool]:
        return [SendEmailTool()]


class ResendIntegration(Integration):
    """Resend (native API) email integration. Same email.send tool —
    SendEmailTool routes to Resend automatically when resend_api_key is
    present, so connecting Resend just works for the agent."""

    integration_id = "resend"
    display_name = "Resend (Email API)"
    required_credentials = RESEND_CREDENTIALS

    def tools(self) -> list[Tool]:
        return [SendEmailTool()]
