"""MVP tool set for the NativeRunner loop (issue #20, PRD D3).

Workspace-scoped file/shell tools only — no browser, no MCP, and no
registry tools in-loop (side-effecting integrations stay on the approval
pipeline). Every tool returns a string *observation*; errors (including
path escapes) come back as observation strings, never exceptions, so the
loop can show the model its mistake and continue.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable
from kompany.core.harness.env_scrub import scrubbed_env

__all__ = ["TOOL_DOCS", "execute_tool", "tool_names"]

SHELL_TIMEOUT_SECONDS = 120.0
MAX_OBSERVATION_CHARS = 4000

TOOL_DOCS = """\
Available tools (args is always a JSON object):

- read_file: {"path": "<relative path>"} — return the file's contents.
- write_file: {"path": "<relative path>", "content": "<full new content>"}
  — create or overwrite a file (parent dirs created automatically).
- list_dir: {"path": "<relative path, optional, default '.'>"} — list
  directory entries (directories end with '/').
- run_shell: {"command": "<one command>", "cwd": "<relative dir, optional>"}
  — run ONE program in the workspace (or in ``cwd`` inside it). There is no
  shell: ``cd``, ``&&``, ``;``, ``|``, ``>`` and globs do not work. Use
  ``cwd`` instead of ``cd`` and one call per command; for anything more
  complex, write a Python script with write_file and run ``python3 it.py``.
  120s timeout; output truncated.

All paths are resolved inside the workspace only — escapes are rejected.
"""


def _truncate(text: str) -> str:
    if len(text) <= MAX_OBSERVATION_CHARS:
        return text
    return (
        text[:MAX_OBSERVATION_CHARS]
        + f"\n... [truncated, {len(text)} chars total]"
    )


def _resolve_in_workspace(workspace: Path, raw: str) -> Path | str:
    """Resolve ``raw`` inside ``workspace``; return an error string on escape."""
    ws = workspace.resolve()
    target = (ws / str(raw)).resolve()
    if not target.is_relative_to(ws):
        return (
            f"ERROR: path {raw!r} escapes the workspace — only paths "
            "inside the workspace are allowed."
        )
    return target


def _read_file(workspace: Path, args: dict[str, Any]) -> str:
    target = _resolve_in_workspace(workspace, args.get("path", ""))
    if isinstance(target, str):
        return target
    try:
        return _truncate(target.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return f"ERROR: cannot read {args.get('path')!r}: {exc}"


def _write_file(workspace: Path, args: dict[str, Any]) -> str:
    target = _resolve_in_workspace(workspace, args.get("path", ""))
    if isinstance(target, str):
        return target
    content = str(args.get("content", ""))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"ERROR: cannot write {args.get('path')!r}: {exc}"
    return f"Wrote {len(content)} chars to {args.get('path')}."


def _list_dir(workspace: Path, args: dict[str, Any]) -> str:
    target = _resolve_in_workspace(workspace, args.get("path", ".") or ".")
    if isinstance(target, str):
        return target
    try:
        entries = sorted(
            e.name + ("/" if e.is_dir() else "") for e in target.iterdir()
        )
    except OSError as exc:
        return f"ERROR: cannot list {args.get('path')!r}: {exc}"
    return _truncate("\n".join(entries) or "(empty directory)")


_SHELL_OPERATORS = frozenset({"&&", "||", ";", "|", ">", ">>", "<", "&"})
_SHELL_BUILTINS = frozenset({"cd", "export", "source", "."})


def _shell_syntax_hint(argv: list[str]) -> str | None:
    """Explain shell syntax up front instead of a bare 'command not found'.

    Agents burned whole turn budgets retrying ``cd x && ...`` or ``pwd;``
    because the error never said there is no shell.
    """
    first = argv[0].rstrip(";")
    if first in _SHELL_BUILTINS:
        return (
            f"ERROR: {first!r} is a shell builtin and run_shell has no shell. "
            "Pass the directory as the 'cwd' argument instead of using cd."
        )
    if any(a in _SHELL_OPERATORS or a.endswith(";") for a in argv):
        return (
            "ERROR: run_shell has no shell, so &&, ||, ;, |, > and < do not work. "
            "Make one call per command (use 'cwd' for the directory), or write a "
            "Python script with write_file and run it."
        )
    return None


def _run_shell(workspace: Path, args: dict[str, Any]) -> str:
    command = str(args.get("command", "")).strip()
    if not command:
        return "ERROR: run_shell requires a non-empty 'command'."
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"ERROR: cannot parse command: {exc}"
    if not argv:
        return "ERROR: run_shell requires a non-empty 'command'."
    shell_hint = _shell_syntax_hint(argv)
    if shell_hint:
        return shell_hint
    cwd = workspace
    if args.get("cwd"):
        resolved = _resolve_in_workspace(workspace, str(args["cwd"]))
        if isinstance(resolved, str):
            return resolved
        if not resolved.is_dir():
            return f"ERROR: cwd {args['cwd']!r} is not a directory in the workspace."
        cwd = resolved
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            # LLM-controlled command: never hand it the engine's API keys /
            # vault key via the inherited environment (security audit).
            env=scrubbed_env(),
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {SHELL_TIMEOUT_SECONDS:.0f}s."
    except FileNotFoundError:
        return f"ERROR: command not found: {argv[0]}"
    except OSError as exc:
        return f"ERROR: cannot run command: {exc}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parts = [f"exit code: {proc.returncode}"]
    if out:
        parts.append(f"stdout:\n{out}")
    if err:
        parts.append(f"stderr:\n{err}")
    return _truncate("\n".join(parts))


_TOOLS: dict[str, Callable[[Path, dict[str, Any]], str]] = {
    "read_file": _read_file,
    "write_file": _write_file,
    "list_dir": _list_dir,
    "run_shell": _run_shell,
}


def tool_names() -> list[str]:
    return sorted(_TOOLS)


def execute_tool(workspace: Path, name: str, args: dict[str, Any]) -> str:
    """Run one tool; ALWAYS returns an observation string (never raises)."""
    fn = _TOOLS.get(name)
    if fn is None:
        return (
            f"ERROR: unknown tool {name!r}. Available: {', '.join(tool_names())}."
        )
    try:
        return fn(Path(workspace), dict(args or {}))
    except Exception as exc:  # noqa: BLE001 — observation, never a crash
        return f"ERROR: tool {name} failed: {type(exc).__name__}: {exc}"
