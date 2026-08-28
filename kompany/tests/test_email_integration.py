"""Email integration (#5) — first real Tool. Agents can SEND, not just draft."""

from __future__ import annotations

from types import SimpleNamespace

from kompany.integrations.email_smtp import (
    EmailIntegration,
    SendEmailInput,
    SendEmailOutput,
    SendEmailTool,
)
from kompany.plugins.contract import AutonomyTier, SideEffect, ToolContext


def _ctx(creds: dict[str, str]) -> ToolContext:
    return ToolContext(
        run_id="r1",
        ledger=None,
        audit=SimpleNamespace(record=lambda *a, **k: None),
        credentials=SimpleNamespace(get=lambda k: creds.get(k)),
        settings=SimpleNamespace(),
    )


def test_email_integration_declares_tool_and_creds():
    integ = EmailIntegration()
    assert integ.integration_id == "email_smtp"
    tools = integ.tools()
    assert [t.name for t in tools] == ["email.send"]
    t = tools[0]
    # External action, must go through approval — never auto-fire.
    assert t.side_effect == SideEffect.EXTERNAL_ACTION
    assert t.autonomy_tier == AutonomyTier.APPROVAL
    assert set(integ.required_credentials) >= {"smtp_host", "smtp_user", "smtp_password"}


def test_send_email_success(monkeypatch):
    sent = {}

    def fake_send(creds, to, subject, body):
        sent.update(to=to, subject=subject, body=body, host=creds["smtp_host"])
        return f"sent to {to}"

    monkeypatch.setattr("kompany.integrations.email_smtp._smtp_send", fake_send)
    out = SendEmailTool().execute(
        SendEmailInput(to="a@b.com", subject="hi", body="hello"),
        _ctx({"smtp_host": "smtp.example.com", "smtp_user": "u", "smtp_password": "p", "smtp_from": "u@x.com"}),
    )
    assert isinstance(out, SendEmailOutput)
    assert out.sent is True
    assert sent["to"] == "a@b.com"


def test_list_integrations_reports_connected(monkeypatch, tmp_path):
    """GET /integrations reports email as connected only when all SMTP
    creds are present in the vault."""
    from fastapi.testclient import TestClient
    from kompany.interfaces import api as api_module

    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    monkeypatch.setenv("KOMPANY_VAULT_KEY", "C8WJOwHdhwcWnW2siGKVyEggFwVHe41ERKC1SFRgfJ8=")
    api_module.reset_engine()
    client = TestClient(api_module.app)

    body = client.get("/integrations").json()
    email = next(i for i in body if i["integration_id"] == "email_smtp")
    assert email["connected"] is False  # nothing stored yet

    eng = api_module.get_engine()
    for k, v in {"smtp_host": "h", "smtp_port": "587", "smtp_user": "u",
                 "smtp_password": "p", "smtp_from": "u@x.com"}.items():
        eng.credentials.set(k, v)
    body = client.get("/integrations").json()
    email = next(i for i in body if i["integration_id"] == "email_smtp")
    assert email["connected"] is True


def test_list_integrations_marks_unsupported_plugin_credential_disconnected(
    monkeypatch, tmp_path
):
    from kompany.core import tool_actions
    from kompany.core.engine import KompanyEngine

    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    engine = KompanyEngine()
    integration = SimpleNamespace(required_credentials=("unsupported_plugin_key",))

    assert tool_actions._integration_connected(engine, integration) is False


def test_propose_then_approve_executes_send(monkeypatch, tmp_path):
    """The deferred-action pipeline (#5): propose → inbox approval →
    approve → real send executes. The action does NOT fire on propose,
    only on approve (founder gate)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    from kompany.core.engine import KompanyEngine

    sent = {}
    monkeypatch.setattr(
        "kompany.integrations.email_smtp._smtp_send",
        lambda creds, to, subject, body: sent.update(to=to) or f"sent to {to}",
    )

    engine = KompanyEngine()
    proposal = engine.propose_action(
        "email.send",
        {"to": "lead@x.com", "subject": "hi", "body": "hello"},
        summary="Send email to lead@x.com",
    )
    # Lands in inbox, NOT sent yet.
    assert "id" in proposal
    assert sent == {}
    inbox = engine.inbox()
    assert any(r["id"] == proposal["id"] and r["action_type"] == "tool_action" for r in inbox)

    # Approve → executes the real send.
    res = engine.approve_request(proposal["id"], approved_by="master")
    assert res["tool_result"]["sent"] is True
    assert sent["to"] == "lead@x.com"


def test_send_routes_to_resend_when_connected(monkeypatch):
    """When resend_api_key is present, email.send uses the Resend API,
    not SMTP — one tool, provider auto-selected."""
    used = {}
    monkeypatch.setattr(
        "kompany.integrations.email_smtp._resend_send",
        lambda key, sender, to, subject, body: used.update(via="resend", to=to) or "sent via Resend (id x)",
    )
    monkeypatch.setattr(
        "kompany.integrations.email_smtp._smtp_send",
        lambda *a, **k: used.update(via="smtp") or "smtp",
    )
    out = SendEmailTool().execute(
        SendEmailInput(to="a@b.com", subject="hi", body="x"),
        _ctx({"resend_api_key": "re_123", "resend_from": "me@dom.com"}),
    )
    assert out.sent is True
    assert used["via"] == "resend"


def test_send_falls_back_to_smtp_without_resend(monkeypatch):
    used = {}
    monkeypatch.setattr(
        "kompany.integrations.email_smtp._smtp_send",
        lambda *a, **k: used.update(via="smtp") or "smtp ok",
    )
    out = SendEmailTool().execute(
        SendEmailInput(to="a@b.com", subject="hi", body="x"),
        _ctx({"smtp_host": "h", "smtp_user": "u", "smtp_password": "p"}),
    )
    assert out.sent is True
    assert used["via"] == "smtp"


def test_send_email_failure_is_honest(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("kompany.integrations.email_smtp._smtp_send", boom)
    out = SendEmailTool().execute(
        SendEmailInput(to="a@b.com", subject="hi", body="x"),
        _ctx({}),
    )
    assert out.sent is False
    assert "connection refused" in out.detail
