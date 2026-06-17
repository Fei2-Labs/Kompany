"""Pluggable in-loop tool layer for the agentic loop (06-16 P1).

Exposes a ``Tool`` abstraction + ``ToolRegistry`` that the NativeRunner
consumes for native tool_use, plus built-in tools (workspace file/shell,
web_fetch/web_search, file_patch).
"""

from __future__ import annotations

from kompany.core.agent_tools.base import (
    FunctionTool,
    SideEffect,
    Tool,
    ToolContext,
)
from kompany.core.agent_tools.browser_tools import (
    browser_tools,
    close_browser_session,
)
from kompany.core.agent_tools.mcp_client import load_mcp_tools, mcp_available
from kompany.core.agent_tools.registry import GATED_PREFIX, ToolRegistry
from kompany.core.agent_tools.workspace_tools import (
    build_default_registry,
    workspace_tools,
)

__all__ = [
    "Tool",
    "FunctionTool",
    "ToolContext",
    "SideEffect",
    "ToolRegistry",
    "GATED_PREFIX",
    "workspace_tools",
    "build_default_registry",
    "browser_tools",
    "close_browser_session",
    "load_mcp_tools",
    "mcp_available",
]
