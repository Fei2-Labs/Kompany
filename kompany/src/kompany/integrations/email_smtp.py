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

import smtplib
import ssl
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
        creds = {k: (ctx.credentials.get(k) or "") for k in REQUIRED_CREDENTIALS}
        try:
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
