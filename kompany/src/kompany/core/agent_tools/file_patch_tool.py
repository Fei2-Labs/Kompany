"""Surgical file-edit tool for the agentic loop (06-16 P1).

Borrowed intent from GenericAgent's ``file_patch``: find a UNIQUE
``old_content`` block (whitespace-exact) in a workspace file and replace
it with ``new_content``. Workspace-scoped like the other file tools;
errors (not found / ambiguous / escape) come back as observation strings.

``SideEffect.WRITE_LOCAL`` — runs inline (local, reversible mutation),
not an external action or spend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kompany.core.agent_tools.base import FunctionTool, SideEffect, ToolContext
from kompany.core.harness.native_tools import _resolve_in_workspace

__all__ = ["file_patch_tool"]


def _file_patch(args: dict[str, Any], ctx: ToolContext) -> str:
    if ctx.workspace is None:
        return "ERROR: file_patch requires a workspace."
    path = str(args.get("path", ""))
    old = args.get("old_content")
    new = args.get("new_content")
    if not path:
        return "ERROR: file_patch requires a 'path'."
    if old is None or new is None:
        return "ERROR: file_patch requires 'old_content' and 'new_content'."
    old = str(old)
    new = str(new)

    target = _resolve_in_workspace(Path(ctx.workspace), path)
    if isinstance(target, str):  # escape error string
        return target
    if not target.exists():
        return f"ERROR: file_patch: {path!r} does not exist."
    try:
        content = target.read_text(encoding="utf-8")
    except OSError as exc:
        return f"ERROR: file_patch cannot read {path!r}: {exc}"

    count = content.count(old)
    if count == 0:
        return (
            f"ERROR: file_patch: old_content not found in {path!r} "
            "(must match whitespace-exactly)."
        )
    if count > 1:
        return (
            f"ERROR: file_patch: old_content matches {count} times in "
            f"{path!r}; make it unique (add surrounding context)."
        )
    updated = content.replace(old, new, 1)
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return f"ERROR: file_patch cannot write {path!r}: {exc}"
    delta = len(updated) - len(content)
    return f"Patched {path}: replaced 1 block ({delta:+d} chars)."


def file_patch_tool() -> FunctionTool:
    return FunctionTool(
        name="file_patch",
        description=(
            "Surgically edit a workspace file: replace a UNIQUE, "
            "whitespace-exact old_content block with new_content. Prefer "
            "this over write_file for targeted edits."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path."},
                "old_content": {
                    "type": "string",
                    "description": "Exact unique block to replace.",
                },
                "new_content": {
                    "type": "string",
                    "description": "Replacement text.",
                },
            },
            "required": ["path", "old_content", "new_content"],
        },
        side_effect=SideEffect.WRITE_LOCAL,
        func=_file_patch,
    )
