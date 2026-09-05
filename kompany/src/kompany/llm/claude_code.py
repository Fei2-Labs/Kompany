"""Local ``claude`` CLI provider — headless ``-p`` mode, subscription auth.

The claude_code provider shells out to the locally installed Claude Code
CLI instead of calling an API. The CLI carries the founder's Claude
subscription auth, so no ANTHROPIC_API_KEY is required. Model ids look
like ``claude-code:sonnet``; the suffix after ``:`` is passed to
``claude --model``.

This module owns only the subprocess mechanics. Cost recording stays in
:class:`kompany.llm.client.LLMClient` (single entry point — see
the internal design spec).
"""

from __future__ import annotations

import json
import os
import subprocess

from kompany.llm.cli_env import cli_child_env


def default_timeout_seconds() -> float:
    """Spawn timeout. 120 s by default (#30: a healthy call takes seconds; a
    300 s blind stall hid the real problem). Override with
    ``KOMPANY_CLI_TIMEOUT_SECONDS``."""
    raw = os.environ.get("KOMPANY_CLI_TIMEOUT_SECONDS", "").strip()
    try:
        return float(raw) if raw else 120.0
    except ValueError:
        return 120.0


DEFAULT_TIMEOUT_SECONDS = default_timeout_seconds()


def run_claude_code(
    model: str, system: str, prompt: str, timeout: float
) -> tuple[str, int, int]:
    """Run one headless ``claude -p`` call.

    Returns ``(text, input_tokens, output_tokens)`` parsed from the
    CLI's JSON output. Raises :class:`RuntimeError` with an actionable
    message when the binary is missing, exits non-zero, times out, or
    emits non-JSON output — the existing provider-error handling in
    ``LLMClient`` wraps it.
    """
    cli_model = model.split(":", 1)[1] if ":" in model else "sonnet"
    cmd = [
        "claude",
        "-p",
        prompt,
        # --system-prompt (not --append-system-prompt): replaces the CLI's
        # default system prompt so the founder's personal CLAUDE.md /
        # output-style customisations can't leak greetings or formatting
        # into agent replies (they break strict-JSON parsing upstream).
        "--system-prompt",
        system,
        "--model",
        cli_model,
        "--output-format",
        "json",
        "--max-turns",
        "1",
        # Single-shot text completion: with tools enabled the model may
        # answer via tool_use, which --max-turns 1 turns into an
        # error_max_turns exit. Disabling tools keeps the reply textual.
        "--tools",
        "",
        # Skip user/project/local settings (output styles, personas):
        # they rewrite replies and break strict-JSON parsing upstream.
        "--setting-sources",
        "",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            # #45/#30: no engine secrets, no nested-harness markers, and an
            # own session so a wedged child cannot hold our process group.
            env=cli_child_env("claude"),
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "claude CLI not found on PATH — install Claude Code or "
            "switch to an API-key provider"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        tail = ""
        for stream in (exc.stderr, exc.stdout):
            if stream:
                tail = (stream.decode("utf-8", "replace") if isinstance(stream, bytes) else str(stream)).strip()[-400:]
                if tail:
                    break
        raise RuntimeError(
            f"claude CLI timed out after {timeout:.0f}s (model={cli_model})"
            + (f"; last output: {tail}" if tail else "; no output at all — check for stale "
               "kompany-mcp/claude processes and raise KOMPANY_CLI_TIMEOUT_SECONDS only if calls are legitimately slow")
        ) from exc
    stderr_tail = (proc.stderr or "").strip()[-500:]
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {proc.returncode} "
            f"(model={cli_model}): {stderr_tail or '(no stderr)'}"
        )
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"claude CLI returned non-JSON output (model={cli_model}): "
            f"{(proc.stdout or '').strip()[:200] or stderr_tail}"
        ) from exc
    usage = payload.get("usage") or {}
    return (
        str(payload.get("result", "")),
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
    )
