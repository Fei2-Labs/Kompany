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
