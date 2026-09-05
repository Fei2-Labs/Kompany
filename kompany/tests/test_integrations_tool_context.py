"""#43: the email test endpoint hands the plugin Tool the full 1.1.0 ToolContext."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kompany.core.engine import KompanyEngine
from kompany.interfaces import api
from kompany.integrations import email_smtp


def test_email_test_endpoint_uses_full_tool_context(monkeypatch):
    engine = KompanyEngine()
    monkeypatch.setattr(api, "_engine", engine)
    for k, v in {"smtp_host": "h", "smtp_port": "587", "smtp_user": "u", "smtp_password": "p", "smtp_from": "f@x"}.items():
        engine.set_credential(k, v)
    seen = {}

    class _Out:
        sent = True
        detail = "captured"
        to = "f@x"

        def model_dump(self):
            return {"sent": True, "detail": "captured", "to": "f@x"}

    def fake_execute(self, inputs, ctx):
        seen["ctx"] = ctx
        return _Out()

    monkeypatch.setattr(email_smtp.SendEmailTool, "execute", fake_execute)
    r = TestClient(api.app).post("/integrations/email/test", json={})
    assert r.status_code == 200, r.text
    ctx = seen["ctx"]
    assert ctx.documents is engine.documents and ctx.artifacts is engine.artifacts
    assert ctx.approvals is engine.approvals and ctx.journal is engine.journal
    assert ctx.events is not None and ctx.ledger is engine.ledger
