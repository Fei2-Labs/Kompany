"""External MCP *client* for the agentic loop (06-16 P4).

Today Kompany only ships an MCP *server* (``interfaces/mcp_server.py``). This
adds the inverse: a client that attaches to founder-configured *external* MCP
servers (stdio or SSE), lists their tools, and exposes each as an in-loop
:class:`Tool` named ``mcp__<server>__<tool>`` that dispatches through the MCP
session.

Design constraints:

- ``mcp`` is an OPTIONAL dependency (``[mcp]`` extra). Imports are guarded; with
  the package absent the loader yields no tools and a health note — it never
  crashes the loop.
- A server that is unreachable / errors during listing is SKIPPED with a health
  note; other servers still load.
- Classification default is SAFE: an unknown MCP tool is ``EXTERNAL_ACTION``
  (gated through ``propose_action``) unless the server or tool is marked
  read-only in config (``read_only: true`` on the server, or a tool name listed
  in the server's ``read_only_tools``).
- The MCP Python SDK is async; tools are dispatched synchronously by the loop,
  so each call runs its own short-lived async session via ``asyncio.run`` in a
  worker thread (robust whether or not an event loop is already running).

Config shape (``settings.mcp_servers`` — a list of dicts)::

    [
      {"name": "fs", "transport": "stdio",
       "command": "uvx", "args": ["mcp-server-filesystem", "/data"],
       "read_only": true},
      {"name": "search", "transport": "sse",
       "url": "https://mcp.example.com/sse",
       "read_only_tools": ["query"]},
    ]
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from typing import Any

from kompany.core.agent_tools.base import SideEffect, ToolContext
from kompany.llm.client_parts._types import ToolSpec

__all__ = [
    "mcp_available",
    "load_mcp_tools",
    "McpServerConfig",
    "McpTool",
    "MCP_TOOL_PREFIX",
]

MCP_TOOL_PREFIX = "mcp__"
_CALL_TIMEOUT_SECONDS = 30.0


def mcp_available() -> bool:
    """Whether the optional ``mcp`` package is importable."""
    try:
        import mcp  # noqa: F401
        import mcp.client.session  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — optional dep
        return False


class McpServerConfig:
    """Normalized config for one external MCP server."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.name = str(raw.get("name") or "").strip()
        self.transport = str(raw.get("transport") or "stdio").strip().lower()
        self.command = raw.get("command")
        self.args = list(raw.get("args") or [])
        self.env = dict(raw.get("env") or {})
        self.url = raw.get("url")
        self.read_only = bool(raw.get("read_only", False))
        self.read_only_tools = set(raw.get("read_only_tools") or [])

    def is_read_only(self, tool_name: str) -> bool:
        return self.read_only or tool_name in self.read_only_tools

    def valid(self) -> bool:
        if not self.name:
            return False
        if self.transport == "stdio":
            return bool(self.command)
        if self.transport == "sse":
            return bool(self.url)
        return False


def _run_async(coro):
    """Run an async coroutine to completion from sync code, robustly.

    Uses a dedicated thread + fresh event loop so it works even when the
    caller already runs inside an event loop (e.g. the API server).
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result(
            timeout=_CALL_TIMEOUT_SECONDS + 5.0
        )


async def _open_session(cfg: McpServerConfig):
    """Async context manager yielding a connected ``ClientSession``.

    Returns an async context manager object the caller ``async with``-es.
    """
    from contextlib import asynccontextmanager

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    @asynccontextmanager
    async def _ctx():
        if cfg.transport == "sse":
            from mcp.client.sse import sse_client

            async with sse_client(cfg.url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        else:
            params = StdioServerParameters(
                command=str(cfg.command), args=cfg.args, env=cfg.env or None
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    return _ctx()


async def _list_tools_async(cfg: McpServerConfig) -> list[dict[str, Any]]:
    ctx = await _open_session(cfg)
    async with ctx as session:
        result = await session.list_tools()
        out = []
        for t in result.tools:
            out.append({
                "name": t.name,
                "description": t.description or "",
                "input_schema": getattr(t, "inputSchema", None)
                or {"type": "object", "properties": {}},
            })
        return out


async def _call_tool_async(cfg: McpServerConfig, tool_name: str, args: dict) -> str:
    ctx = await _open_session(cfg)
    async with ctx as session:
        result = await asyncio.wait_for(
            session.call_tool(tool_name, args or {}),
            timeout=_CALL_TIMEOUT_SECONDS,
        )
        return _stringify_result(result)


def _stringify_result(result: Any) -> str:
    """Flatten an MCP CallToolResult into an observation string."""
    parts: list[str] = []
    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(str(text))
        else:
            parts.append(json.dumps(_to_plain(item), ensure_ascii=False, default=str))
    if getattr(result, "isError", False):
        return "ERROR (mcp tool): " + ("\n".join(parts) or "(no detail)")
    return "\n".join(parts) or "(mcp tool returned no content)"


def _to_plain(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:  # noqa: BLE001
            pass
    return str(obj)


class McpTool:
    """In-loop tool backed by one tool on one external MCP server."""

    def __init__(self, cfg: McpServerConfig, spec: dict[str, Any]) -> None:
        self._cfg = cfg
        self._tool_name = spec["name"]
        self._description = spec.get("description", "")
        self._parameters = spec.get("input_schema") or {
            "type": "object", "properties": {}
        }
        self._side_effect = (
            SideEffect.READ if cfg.is_read_only(self._tool_name)
            else SideEffect.EXTERNAL_ACTION
        )

    @property
    def name(self) -> str:
        return f"{MCP_TOOL_PREFIX}{self._cfg.name}__{self._tool_name}"

    @property
    def description(self) -> str:
        tag = "" if self._side_effect is SideEffect.READ else " (SIDE EFFECT — gated)"
        return f"[MCP:{self._cfg.name}] {self._description}{tag}"

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def side_effect(self) -> SideEffect:
        return self._side_effect

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        """Inline path (read-only MCP tools). Gated ones never reach here."""
        try:
            return _run_async(_call_tool_async(self._cfg, self._tool_name, args))
        except Exception as exc:  # noqa: BLE001 — observation, never a crash
            return (
                f"ERROR: MCP tool {self.name} failed: "
                f"{type(exc).__name__}: {exc}"
            )

    def to_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )


def load_mcp_tools(settings: Any) -> tuple[list[McpTool], list[str]]:
    """Build :class:`McpTool` instances for all configured external servers.

    Returns ``(tools, health_notes)``. Never raises:

    - ``mcp`` package missing → no tools + a single note.
    - a server that is invalid / unreachable / errors on list → skipped + note.
    """
    notes: list[str] = []
    raw_servers = list(getattr(settings, "mcp_servers", None) or [])
    if not raw_servers:
        return [], notes
    if not mcp_available():
        notes.append(
            "MCP client unavailable: the optional 'mcp' package is not "
            "installed; external MCP servers were skipped."
        )
        return [], notes

    tools: list[McpTool] = []
    for raw in raw_servers:
        try:
            cfg = McpServerConfig(raw if isinstance(raw, dict) else dict(raw))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"MCP server config invalid ({exc}); skipped.")
            continue
        if not cfg.valid():
            notes.append(
                f"MCP server {cfg.name or '(unnamed)'!r} has invalid "
                f"{cfg.transport!r} config; skipped."
            )
            continue
        try:
            specs = _run_async(_list_tools_async(cfg))
        except Exception as exc:  # noqa: BLE001 — unreachable server, skip
            notes.append(
                f"MCP server {cfg.name!r} unreachable "
                f"({type(exc).__name__}: {exc}); skipped."
            )
            continue
        for spec in specs:
            tools.append(McpTool(cfg, spec))
    return tools, notes
