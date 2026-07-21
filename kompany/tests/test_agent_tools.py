"""Pluggable tool registry + built-in tools tests (06-16 P1)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kompany.core.agent_tools import (
    FunctionTool,
    GATED_PREFIX,
    SideEffect,
    ToolContext,
    ToolRegistry,
    build_default_registry,
    workspace_tools,
)
from kompany.core.agent_tools.file_patch_tool import file_patch_tool
from kompany.core.agent_tools.web_tools import web_fetch_tool, web_search_tool


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(workspace=tmp_path, settings=None)


# --------------------------------------------------------------------- #
# registry dispatch + classification
# --------------------------------------------------------------------- #

def test_default_registry_has_seven_tools():
    reg = build_default_registry()
    assert set(reg.names()) == {
        "read_file", "write_file", "list_dir", "run_shell",
        "web_fetch", "web_search", "file_patch",
    }


def test_specs_render_native_schemas():
    reg = build_default_registry()
    specs = {s.name: s for s in reg.specs()}
    assert specs["web_fetch"].parameters["required"] == ["url"]


def test_read_only_classification_runs_inline(tmp_path):
    reg = build_default_registry()
    assert reg.is_inline("web_fetch")  # READ
    assert reg.is_inline("write_file")  # WRITE_LOCAL
    assert reg.is_inline("file_patch")  # WRITE_LOCAL


def test_external_action_tool_is_gated(ctx):
    def _boom(args, c):  # should never run
        raise AssertionError("gated tool must not execute")

    reg = ToolRegistry([
        FunctionTool(
            name="email.send",
            description="send email",
            parameters={"type": "object", "properties": {}},
            side_effect=SideEffect.EXTERNAL_ACTION,
            func=_boom,
        )
    ])
    assert not reg.is_inline("email.send")
    obs = reg.dispatch("email.send", {}, ctx)
    assert obs.startswith(GATED_PREFIX)
    assert "external_action" in obs


def test_spend_tool_is_gated(ctx):
    reg = ToolRegistry([
        FunctionTool(
            name="ads.buy",
            description="buy ads",
            parameters={"type": "object", "properties": {}},
            side_effect=SideEffect.SPEND,
            func=lambda a, c: "spent",
        )
    ])
    obs = reg.dispatch("ads.buy", {}, ctx)
    assert obs.startswith(GATED_PREFIX)


def test_gated_tool_routes_through_propose_action(tmp_path):
    """06-16 P2: with an engine handle, a gated tool calls propose_action,
    does NOT run inline, and records the created approval id on the ctx."""
    calls = {}

    class FakeEngine:
        def propose_action(self, tool_name, inputs, summary, **kw):
            calls["tool_name"] = tool_name
            calls["inputs"] = inputs
            return {"id": "appr-9", "tool_name": tool_name}

    ctx = ToolContext(
        workspace=tmp_path, settings=None, engine=FakeEngine(),
        extra={"pending_approval_ids": []},
    )

    def _must_not_run(args, c):
        raise AssertionError("gated tool must not run inline")

    reg = ToolRegistry([
        FunctionTool(
            name="email.send", description="send",
            parameters={"type": "object", "properties": {}},
            side_effect=SideEffect.EXTERNAL_ACTION, func=_must_not_run,
        )
    ])
    obs = reg.dispatch("email.send", {"to": "x@y.z"}, ctx)
    assert obs.startswith(GATED_PREFIX)
    assert "appr-9" in obs
    assert calls["tool_name"] == "email.send"
    assert ctx.extra["pending_approval_ids"] == ["appr-9"]


def test_unknown_tool_returns_observation(ctx):
    reg = build_default_registry()
    obs = reg.dispatch("does_not_exist", {}, ctx)
    assert obs.startswith("ERROR: unknown tool")


def test_tool_exception_becomes_observation(ctx):
    reg = ToolRegistry([
        FunctionTool(
            name="boom",
            description="x",
            parameters={"type": "object", "properties": {}},
            side_effect=SideEffect.READ,
            func=lambda a, c: (_ for _ in ()).throw(RuntimeError("kaboom")),
        )
    ])
    obs = reg.dispatch("boom", {}, ctx)
    assert "kaboom" in obs and obs.startswith("ERROR:")


# --------------------------------------------------------------------- #
# workspace tools delegate to native_tools (refactor, not duplicate)
# --------------------------------------------------------------------- #

def test_workspace_write_then_read(ctx):
    reg = ToolRegistry(workspace_tools())
    reg.dispatch("write_file", {"path": "a.txt", "content": "hi"}, ctx)
    obs = reg.dispatch("read_file", {"path": "a.txt"}, ctx)
    assert obs == "hi"


def test_workspace_path_escape_is_observation(ctx):
    reg = ToolRegistry(workspace_tools())
    obs = reg.dispatch("read_file", {"path": "../x"}, ctx)
    assert "escapes the workspace" in obs


# --------------------------------------------------------------------- #
# file_patch
# --------------------------------------------------------------------- #

def test_file_patch_unique_replace(ctx):
    (Path(ctx.workspace) / "f.py").write_text("a = 1\nb = 2\nc = 3\n")
    obs = file_patch_tool().run(
        {"path": "f.py", "old_content": "b = 2", "new_content": "b = 20"}, ctx
    )
    assert "Patched" in obs
    assert (Path(ctx.workspace) / "f.py").read_text() == "a = 1\nb = 20\nc = 3\n"


def test_file_patch_not_found(ctx):
    (Path(ctx.workspace) / "f.py").write_text("a = 1\n")
    obs = file_patch_tool().run(
        {"path": "f.py", "old_content": "zzz", "new_content": "q"}, ctx
    )
    assert "not found" in obs


def test_file_patch_ambiguous(ctx):
    (Path(ctx.workspace) / "f.py").write_text("x\nx\n")
    obs = file_patch_tool().run(
        {"path": "f.py", "old_content": "x", "new_content": "y"}, ctx
    )
    assert "matches 2 times" in obs


def test_file_patch_missing_file(ctx):
    obs = file_patch_tool().run(
        {"path": "nope.py", "old_content": "a", "new_content": "b"}, ctx
    )
    assert "does not exist" in obs


# --------------------------------------------------------------------- #
# web_fetch (mocked httpx) + web_search (unconfigured)
# --------------------------------------------------------------------- #

def test_web_fetch_strips_html(monkeypatch, ctx):
    def fake_get(url, **kwargs):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><h1>Hi</h1><script>x()</script><p>World</p></body></html>",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    obs = web_fetch_tool().run({"url": "https://example.test"}, ctx)
    assert "Hi" in obs and "World" in obs
    assert "x()" not in obs  # script stripped
    assert "200" in obs


def test_web_fetch_rejects_non_http(ctx):
    obs = web_fetch_tool().run({"url": "ftp://x"}, ctx)
    assert obs.startswith("ERROR")


def test_web_fetch_network_error_is_observation(monkeypatch, ctx):
    def fake_get(url, **kwargs):
        raise httpx.ConnectError("boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    obs = web_fetch_tool().run({"url": "https://x.test"}, ctx)
    assert obs.startswith("ERROR: web_fetch failed")


def test_web_search_unconfigured_graceful(ctx):
    obs = web_search_tool().run({"query": "kompany"}, ctx)
    assert "unconfigured" in obs
    assert obs.lower().count("error") == 0  # graceful, not an error


def test_web_search_uses_key_when_configured(monkeypatch):
    settings = SimpleNamespace(tavily_api_key="tvly-key")
    ctx = ToolContext(workspace=None, settings=settings)

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return httpx.Response(
            200,
            json={"results": [
                {"title": "T", "url": "https://r.test", "content": "snippet"},
            ]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    obs = web_search_tool().run({"query": "kompany"}, ctx)
    assert captured["json"]["api_key"] == "tvly-key"
    assert captured["json"]["query"] == "kompany"
    assert "https://r.test" in obs
