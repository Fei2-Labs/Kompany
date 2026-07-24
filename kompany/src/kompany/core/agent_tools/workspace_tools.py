"""Built-in workspace tools as registry ``Tool`` instances (06-16 P1).

The four original NativeRunner tools (read_file/write_file/list_dir/
run_shell) are refactored — NOT duplicated — by wrapping the existing
``native_tools`` implementations. This keeps the workspace-scoping,
truncation and path-escape behaviour byte-identical while exposing them
through the pluggable registry with native tool schemas + a
``SideEffect`` classification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kompany.core.agent_tools.base import FunctionTool, SideEffect, ToolContext
from kompany.core.harness import native_tools

__all__ = ["workspace_tools", "build_default_registry"]


def _ws(ctx: ToolContext) -> Path:
    if ctx.workspace is None:
        raise ValueError("workspace tool requires ctx.workspace")
    return Path(ctx.workspace)


def _read_file(args: dict[str, Any], ctx: ToolContext) -> str:
    return native_tools.execute_tool(_ws(ctx), "read_file", args)


def _write_file(args: dict[str, Any], ctx: ToolContext) -> str:
    return native_tools.execute_tool(_ws(ctx), "write_file", args)


def _list_dir(args: dict[str, Any], ctx: ToolContext) -> str:
    return native_tools.execute_tool(_ws(ctx), "list_dir", args)


def _run_shell(args: dict[str, Any], ctx: ToolContext) -> str:
    return native_tools.execute_tool(_ws(ctx), "run_shell", args)


def workspace_tools() -> list[FunctionTool]:
    """The four always-on workspace tools."""
    return [
        FunctionTool(
            name="read_file",
            description="Read a workspace file's contents (relative path).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path."},
                },
                "required": ["path"],
            },
            side_effect=SideEffect.READ,
            func=_read_file,
        ),
        FunctionTool(
            name="write_file",
            description=(
                "Create or overwrite a workspace file with full new content "
                "(parent dirs created automatically)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path."},
                    "content": {"type": "string", "description": "Full new content."},
                },
                "required": ["path", "content"],
            },
            side_effect=SideEffect.WRITE_LOCAL,
            func=_write_file,
        ),
        FunctionTool(
            name="list_dir",
            description="List entries in a workspace directory (default '.').",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative dir, default '.'."},
                },
            },
            side_effect=SideEffect.READ,
            func=_list_dir,
        ),
        FunctionTool(
            name="run_shell",
            description=(
                "Run a command string in the workspace (no shell "
                "interpolation; 120s timeout; output truncated)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command string."},
                },
                "required": ["command"],
            },
            # WRITE_LOCAL: a shell command can mutate the workspace but is
            # not an external action or spend — runs inline like before.
            side_effect=SideEffect.WRITE_LOCAL,
            func=_run_shell,
        ),
    ]


def build_default_registry():
    """Registry with the 4 workspace tools + the 3 P1 read-only tools."""
    # Imported here to avoid a cycle at module import time.
    from kompany.core.agent_tools.registry import ToolRegistry
    from kompany.core.agent_tools.web_tools import web_tools
    from kompany.core.agent_tools.file_patch_tool import file_patch_tool

    reg = ToolRegistry(workspace_tools())
    for tool in web_tools():
        reg.register(tool)
    reg.register(file_patch_tool())
    return reg


def build_harness_registry(engine: Any) -> "ToolRegistry":
    """Registry for the NativeRunner harness path.

    Same as :func:`build_default_registry` (workspace + web + file_patch)
    PLUS every registered ``kompany.integrations`` tool, wrapped in
    :class:`IntegrationToolAdapter` so the model can call plugin tools
    (``email.send``, ``linkedin.feed``, …) in-loop. READ plugin tools run
    inline via ``engine.execute_tool``; side-effecting ones route to
    ``engine.propose_action`` through the registry's gate — the same seam
    ``build_chat_registry`` uses for the CEO chat path.

    A broken integration degrades to fewer tools, never an exception that
    blocks the harness loop (defensive — mirrors ``build_chat_registry``).
    """
    reg = build_default_registry()
    try:
        from kompany.core.agent_tools.chat_registry import IntegrationToolAdapter
        from kompany.core import tool_actions

        for entry in tool_actions.tool_registry(engine).values():
            plugin_tool = entry.get("tool")
            if plugin_tool is None:
                continue
            reg.register(IntegrationToolAdapter(plugin_tool))
    except Exception:  # noqa: BLE001 — a broken integration can't block harness
        pass
    return reg
