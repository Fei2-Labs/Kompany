"""Sidecar REST bridge for MCP tool calls — ``POST /mcp/tool``.

When ``kompany-mcp`` detects a running sidecar (see ``mcp_proxy.py``)
it forwards every tool call here so the work executes inside the
sidecar's single engine. The handler reuses the exact
:func:`dispatch_tool` the stdio MCP server uses, so tool behavior is
identical in proxy and in-process modes by construction.

Security posture: covered by ``interfaces/api_guard.py`` like every other
route — origin-checked always, token-gated when ``web_dashboard_token`` is
set (the stdio ``kompany-mcp`` proxy runs on the same machine and reads the
token from settings).

Long calls: ``kompany_execute`` can run for minutes. The handler is a
sync ``def`` so FastAPI runs it in the threadpool, and uvicorn does not
time out handlers — no server-side timeout configuration is needed.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from kompany.interfaces.mcp_dispatch import UnknownToolError, dispatch_tool

router = APIRouter()


class McpToolRequest(BaseModel):
    """Wire body for one proxied MCP tool call."""

    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/mcp/tool")
def mcp_tool(req: McpToolRequest) -> JSONResponse:
    """Execute one MCP tool inside the sidecar engine."""
    # Lazy import: api.py includes this router at module load time, so a
    # top-level get_engine import would be circular.
    from kompany.interfaces.api import get_engine

    try:
        result = dispatch_tool(get_engine(), req.name, req.arguments)
    except UnknownToolError:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"Unknown tool: {req.name}"},
        )
    except Exception as exc:  # noqa: BLE001 — surface to the proxy, never mask
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": f"{type(exc).__name__}: {exc}"},
        )
    # Normalize through the exact serializer the stdio path uses
    # (json.dumps with default=str) so proxy-mode output is
    # byte-identical to in-process mode for the same payload.
    return JSONResponse(
        content={"ok": True, "result": json.loads(json.dumps(result, default=str))},
    )
