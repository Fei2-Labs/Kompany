"""P4 real-world tool reach: browser (Edge CDP), MCP client, integrations.

All backends are mocked — NO real browser, NO real MCP server, NO network.
Covers:
- browser tool classification (navigate/read/find/screenshot inline vs
  click/type gated), and graceful degrade when Playwright/Edge are absent,
- a mocked Playwright-over-CDP session driving read + (gated) write tools,
- MCP client registers external tools, dispatches via a mocked session, gates
  by default, honors read_only config, and is graceful on an unreachable
  server / missing package,
- integration tools appear in-loop via build_chat_registry and gate.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kompany.core.agent_tools import (
    GATED_PREFIX,
    SideEffect,
    ToolContext,
    ToolRegistry,
    browser_tools,
)
import importlib

from kompany.core.agent_tools.browser_tools import (
    BrowserSession,
    close_browser_session,
)

# The package __init__ re-exports ``browser_tools`` (the factory function),
# which shadows the submodule on ``import ...browser_tools as m``; load the
# module object explicitly so monkeypatch targets the real module.
bt_module = importlib.import_module(
    "kompany.core.agent_tools.browser_tools"
)
from kompany.core.agent_tools import mcp_client as mcp_module
from kompany.core.agent_tools.mcp_client import (
    McpServerConfig,
    McpTool,
    load_mcp_tools,
)
from kompany.core.agent_tools.chat_registry import build_chat_registry


# ===================================================================== #
# Browser tools
# ===================================================================== #

def test_browser_tools_classification():
    reg = ToolRegistry(list(browser_tools()))
    assert set(reg.names()) == {
        "browser_navigate", "browser_read", "browser_find",
        "browser_screenshot", "browser_click", "browser_type",
    }
    # READ (inline)
    for n in ("browser_navigate", "browser_read", "browser_find",
              "browser_screenshot"):
        assert reg.is_inline(n), n
    # EXTERNAL_ACTION (gated)
    for n in ("browser_click", "browser_type"):
        assert not reg.is_inline(n), n


def test_browser_write_tools_gate_through_propose_action():
    calls = {}

    class FakeEngine:
        def propose_action(self, tool_name, inputs, summary, **kw):
            calls["tool_name"] = tool_name
            return {"id": "appr-b1"}

    ctx = ToolContext(settings=None, engine=FakeEngine(),
                      extra={"pending_approval_ids": []})
    reg = ToolRegistry(list(browser_tools()))
    obs = reg.dispatch("browser_click", {"target": "#buy"}, ctx)
    assert obs.startswith(GATED_PREFIX)
    assert calls["tool_name"] == "browser_click"
    assert ctx.extra["pending_approval_ids"] == ["appr-b1"]


def test_browser_graceful_degrade_when_playwright_absent(monkeypatch):
    """No Playwright installed → READ browser tool returns a clear notice,
    never crashes the loop."""
    monkeypatch.setattr(bt_module, "_playwright_available", lambda: False)
    ctx = ToolContext(settings=None, extra={})
    reg = ToolRegistry(list(browser_tools()))
    obs = reg.dispatch("browser_navigate", {"url": "https://x.test"}, ctx)
    assert "browser unavailable" in obs
    assert "playwright" in obs.lower()


def test_browser_graceful_degrade_when_edge_not_running(monkeypatch):
    """Playwright present but Edge-on-9223 absent → connect raises → clear
    notice, no crash."""
    monkeypatch.setattr(bt_module, "_playwright_available", lambda: True)

    class _PW:
        def start(self):
            return self

        @property
        def chromium(self):
            class _C:
                def connect_over_cdp(self, endpoint):
                    raise ConnectionRefusedError("no Edge on 9223")
            return _C()

        def stop(self):
            pass

    import sys
    fake_mod = SimpleNamespace(sync_playwright=lambda: _PW())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_mod)

    ctx = ToolContext(settings=None, extra={})
    reg = ToolRegistry(list(browser_tools()))
    obs = reg.dispatch("browser_read", {}, ctx)
    assert "browser unavailable" in obs
    assert "CDP" in obs


def _install_fake_playwright(monkeypatch, page):
    """Wire a fake Playwright-over-CDP whose contexts[0].pages[0] == page."""
    monkeypatch.setattr(bt_module, "_playwright_available", lambda: True)

    class _Ctx:
        def __init__(self):
            self.pages = [page]

    class _Browser:
        def __init__(self):
            self.contexts = [_Ctx()]
            self.closed = False

        def close(self):
            self.closed = True

    browser = _Browser()

    class _Chromium:
        def connect_over_cdp(self, endpoint):
            return browser

    class _PW:
        def __init__(self):
            self.chromium = _Chromium()
            self.stopped = False

        def start(self):
            return self

        def stop(self):
            self.stopped = True

    pw = _PW()
    import sys
    fake_mod = SimpleNamespace(sync_playwright=lambda: pw)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_mod)
    return browser, pw


def test_browser_read_navigate_via_mocked_cdp(monkeypatch):
    class _Page:
        url = "https://acme.test/dash"

        def goto(self, url, **kw):
            type(self).url = url

        def title(self):
            return "Dashboard"

        def inner_text(self, sel):
            return "Revenue 42k"

    page = _Page()
    _install_fake_playwright(monkeypatch, page)
    ctx = ToolContext(settings=None, extra={})
    reg = ToolRegistry(list(browser_tools()))

    nav = reg.dispatch("browser_navigate", {"url": "https://acme.test/login"}, ctx)
    assert "navigated to" in nav and "Dashboard" in nav
    read = reg.dispatch("browser_read", {}, ctx)
    assert "Revenue 42k" in read


def test_browser_session_reused_and_closed(monkeypatch):
    class _Page:
        url = "https://x.test"

        def inner_text(self, sel):
            return "ok"

    page = _Page()
    browser, pw = _install_fake_playwright(monkeypatch, page)
    ctx = ToolContext(settings=None, extra={})
    reg = ToolRegistry(list(browser_tools()))

    reg.dispatch("browser_read", {}, ctx)
    sess1 = ctx.extra["browser_session"]
    reg.dispatch("browser_read", {}, ctx)
    sess2 = ctx.extra["browser_session"]
    assert sess1 is sess2  # single reusable session per run
    assert isinstance(sess1, BrowserSession)

    close_browser_session(ctx)
    assert "browser_session" not in ctx.extra
    assert browser.closed and pw.stopped  # detached cleanly


# ===================================================================== #
# MCP client
# ===================================================================== #

def test_mcp_config_classification_default_external_vs_read_only():
    cfg_default = McpServerConfig({"name": "srv", "transport": "stdio",
                                   "command": "x"})
    tool = McpTool(cfg_default, {"name": "do_thing", "description": "d",
                                 "input_schema": {"type": "object"}})
    assert tool.name == "mcp__srv__do_thing"
    assert tool.side_effect is SideEffect.EXTERNAL_ACTION  # safe default

    cfg_ro = McpServerConfig({"name": "srv", "transport": "stdio",
                              "command": "x", "read_only": True})
    ro_tool = McpTool(cfg_ro, {"name": "query", "description": "d"})
    assert ro_tool.side_effect is SideEffect.READ

    cfg_partial = McpServerConfig({"name": "srv", "transport": "stdio",
                                   "command": "x",
                                   "read_only_tools": ["query"]})
    assert McpTool(cfg_partial, {"name": "query"}).side_effect is SideEffect.READ
    assert McpTool(cfg_partial, {"name": "write"}).side_effect \
        is SideEffect.EXTERNAL_ACTION


def test_mcp_loads_tools_and_dispatches(monkeypatch):
    """load_mcp_tools lists tools (mocked); a read-only tool dispatches via
    the mocked async session and returns its content."""
    settings = SimpleNamespace(mcp_servers=[
        {"name": "fs", "transport": "stdio", "command": "x",
         "read_only": True},
    ])
    monkeypatch.setattr(mcp_module, "mcp_available", lambda: True)
    monkeypatch.setattr(
        mcp_module, "_list_tools_async",
        _make_async([{"name": "read_file", "description": "rf",
                      "input_schema": {"type": "object"}}]),
    )
    tools, notes = load_mcp_tools(settings)
    assert notes == []
    assert [t.name for t in tools] == ["mcp__fs__read_file"]
    assert tools[0].side_effect is SideEffect.READ

    monkeypatch.setattr(mcp_module, "_call_tool_async", _make_async("file body"))
    obs = tools[0].run({"path": "a"}, ToolContext(settings=None, extra={}))
    assert obs == "file body"


def test_mcp_tool_gates_by_default(monkeypatch):
    settings = SimpleNamespace(mcp_servers=[
        {"name": "ops", "transport": "stdio", "command": "x"},  # not read-only
    ])
    monkeypatch.setattr(mcp_module, "mcp_available", lambda: True)
    monkeypatch.setattr(
        mcp_module, "_list_tools_async",
        _make_async([{"name": "deploy", "description": "d"}]),
    )
    tools, _ = load_mcp_tools(settings)
    reg = ToolRegistry(list(tools))
    assert not reg.is_inline("mcp__ops__deploy")

    calls = {}

    class FakeEngine:
        def propose_action(self, tool_name, inputs, summary, **kw):
            calls["t"] = tool_name
            return {"id": "appr-m1"}

    ctx = ToolContext(settings=None, engine=FakeEngine(),
                      extra={"pending_approval_ids": []})
    obs = reg.dispatch("mcp__ops__deploy", {}, ctx)
    assert obs.startswith(GATED_PREFIX)
    assert calls["t"] == "mcp__ops__deploy"


def test_mcp_graceful_when_server_unreachable(monkeypatch):
    settings = SimpleNamespace(mcp_servers=[
        {"name": "down", "transport": "stdio", "command": "x"},
    ])
    monkeypatch.setattr(mcp_module, "mcp_available", lambda: True)

    def _boom(cfg):
        raise ConnectionError("server is down")

    monkeypatch.setattr(mcp_module, "_list_tools_async",
                        _make_async_raises(_boom))
    tools, notes = load_mcp_tools(settings)
    assert tools == []
    assert any("unreachable" in n for n in notes)


def test_mcp_graceful_when_package_absent(monkeypatch):
    settings = SimpleNamespace(mcp_servers=[
        {"name": "x", "transport": "stdio", "command": "x"},
    ])
    monkeypatch.setattr(mcp_module, "mcp_available", lambda: False)
    tools, notes = load_mcp_tools(settings)
    assert tools == []
    assert any("not installed" in n for n in notes)


def test_mcp_no_servers_configured_is_silent():
    tools, notes = load_mcp_tools(SimpleNamespace(mcp_servers=[]))
    assert tools == [] and notes == []


def test_mcp_invalid_config_skipped(monkeypatch):
    settings = SimpleNamespace(mcp_servers=[
        {"name": "", "transport": "stdio"},          # no name
        {"name": "sse_no_url", "transport": "sse"},  # no url
    ])
    monkeypatch.setattr(mcp_module, "mcp_available", lambda: True)
    tools, notes = load_mcp_tools(settings)
    assert tools == []
    assert len(notes) == 2


# ===================================================================== #
# Integrations in-loop via build_chat_registry
# ===================================================================== #

def test_integration_tools_appear_in_loop_and_gate(monkeypatch):
    """email.send (an EXTERNAL_ACTION integration tool) shows up in the chat
    registry and gates through propose_action."""
    from kompany.core import tool_actions

    class _EmailTool:
        name = "email.send"
        description = "Send an email"
        side_effect = SideEffect.EXTERNAL_ACTION
        input_schema = None

    monkeypatch.setattr(
        tool_actions, "tool_registry",
        lambda engine: {"email.send": {"tool": _EmailTool(),
                                       "integrations": []}},
    )

    calls = {}

    class FakeEngine:
        settings = SimpleNamespace(mcp_servers=[])
        audit = SimpleNamespace(record=lambda *a, **k: None)

        def propose_action(self, tool_name, inputs, summary, **kw):
            calls["t"] = tool_name
            return {"id": "appr-e1"}

    engine = FakeEngine()
    reg = build_chat_registry(engine)
    # Plugin tool names are sanitized for the model-facing schema
    # (OpenAI's ``^[a-zA-Z0-9_-]+$`` pattern rejects dots): ``email.send``
    # becomes ``email_send`` in the registry. The ORIGINAL dotted name is
    # preserved on ``dispatch_name`` and used when the registry calls
    # ``engine.propose_action`` / ``engine.execute_tool``.
    assert "email_send" in reg.names()
    assert "email.send" not in reg.names()
    assert not reg.is_inline("email_send")
    # browser tools also present (P4 source composition)
    assert "browser_navigate" in reg.names()

    ctx = ToolContext(settings=engine.settings, engine=engine,
                      extra={"pending_approval_ids": []})
    obs = reg.dispatch("email_send", {"to": "x@y.z"}, ctx)
    assert obs.startswith(GATED_PREFIX)
    # Engine-side dispatch uses the ORIGINAL dotted name, not the sanitized one.
    assert calls["t"] == "email.send"
    assert ctx.extra["pending_approval_ids"] == ["appr-e1"]


def test_build_chat_registry_survives_broken_integration_loader(monkeypatch):
    from kompany.core import tool_actions

    def _boom(engine):
        raise RuntimeError("integration loader exploded")

    monkeypatch.setattr(tool_actions, "tool_registry", _boom)
    engine = SimpleNamespace(settings=SimpleNamespace(mcp_servers=[]),
                             audit=SimpleNamespace(record=lambda *a, **k: None))
    reg = build_chat_registry(engine)
    # web + browser still there despite the integration source blowing up
    assert "web_fetch" in reg.names()
    assert "browser_navigate" in reg.names()


# ===================================================================== #
# async mock helpers
# ===================================================================== #

def _make_async(return_value):
    async def _coro(*args, **kwargs):
        return return_value
    return _coro


def _make_async_raises(raiser):
    async def _coro(cfg):
        return raiser(cfg)
    return _coro
