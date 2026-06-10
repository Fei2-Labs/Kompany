"""Shared MCP tool dispatch — single source of truth for every transport.

Both the stdio MCP server (``mcp_server.py``) and the sidecar REST
bridge (``mcp_bridge.py``) execute tools through :func:`dispatch_tool`,
so tool behavior is identical in proxy and in-process modes by
construction.
"""

from __future__ import annotations

from kompany.interfaces.mcp_dispatch.dispatcher import dispatch_tool
from kompany.interfaces.mcp_dispatch.governance import UnknownToolError

__all__ = ["UnknownToolError", "dispatch_tool"]
