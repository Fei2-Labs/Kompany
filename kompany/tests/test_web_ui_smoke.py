"""Smoke tests for the ``kompany serve`` CLI command and audit/SSE wiring."""

from __future__ import annotations

import asyncio

import pytest
from typer.testing import CliRunner

from kompany.core.event_hub import EventHub, get_event_hub, reset_event_hub
from kompany.interfaces.cli import app as cli_app


runner = CliRunner()


def test_serve_cli_calls_uvicorn(monkeypatch):
    """``kompany serve`` should call into uvicorn.run with the FastAPI app."""
    captured = {}

    def fake_run(target, host, port, reload, log_level):
        captured["target"] = target
        captured["host"] = host
        captured["port"] = port
        captured["reload"] = reload

    # Patch the uvicorn module that the command imports lazily.
    import sys
    import types

    fake_uvicorn = types.SimpleNamespace(run=fake_run)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    result = runner.invoke(
        cli_app,
        ["serve", "--host", "127.0.0.1", "--port", "9999"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["target"] == "kompany.interfaces.api:app"
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9999
    assert captured["reload"] is False


def test_serve_open_flag_triggers_webbrowser(monkeypatch):
    opened = {}
    threads = []

    import sys
    import types

    fake_uvicorn = types.SimpleNamespace(run=lambda *a, **kw: None)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    # Replace threading.Timer so the callback fires immediately.
    import threading as _t

    class _FakeTimer:
        def __init__(self, delay, fn):
            self._fn = fn
        def start(self):
            self._fn()

    monkeypatch.setattr(_t, "Timer", _FakeTimer)

    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.setdefault("url", url))

    result = runner.invoke(
        cli_app,
        ["serve", "--host", "127.0.0.1", "--port", "1234", "--open"],
    )
    assert result.exit_code == 0, result.stdout
    assert opened.get("url") == "http://127.0.0.1:1234/ui/"


@pytest.mark.asyncio
async def test_audit_record_publishes_sse_event(tmp_path):
    """``AuditLog.record`` should fan out an ``audit.<event_type>`` envelope."""
    reset_event_hub()
    hub = get_event_hub()

    from kompany.state.database import Database
    from kompany.state.audit import AuditLog

    db = Database(tmp_path)
    audit = AuditLog(db)

    received: list[dict] = []

    async def collect():
        gen = hub.subscribe()
        try:
            evt = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
            received.append(evt)
        finally:
            await gen.aclose()

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)

    audit.record(
        event_type="ui.test",
        action="hello",
        detail={"foo": "bar"},
        agent_role="cfo",
    )

    await task
    assert len(received) == 1
    envelope = received[0]
    assert envelope["type"] == "audit.ui.test"
    assert envelope["data"]["action"] == "hello"
    assert envelope["data"]["agent_role"] == "cfo"


@pytest.mark.asyncio
async def test_approvals_create_publishes_inbox_updated(tmp_path):
    reset_event_hub()
    hub = get_event_hub()

    from kompany.state.database import Database
    from kompany.state.approvals import ApprovalRequests
    from kompany.state.models import ApprovalRequest

    db = Database(tmp_path)
    approvals = ApprovalRequests(db)

    received: list[dict] = []

    async def collect():
        gen = hub.subscribe()
        try:
            evt = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
            received.append(evt)
        finally:
            await gen.aclose()

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)

    req = ApprovalRequest(
        action_type="test.approval",
        summary="test summary",
        severity="medium",
        requested_by="cfo",
    )
    approvals.create(req)

    await task
    assert len(received) == 1
    assert received[0]["type"] == "inbox.updated"
    assert received[0]["data"]["reason"] == "created"
