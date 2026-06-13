"""Generic single-shot CLI providers — codex / opencode, subscription auth.

Like :mod:`kompany.llm.claude_code`, these providers shell out to a
locally installed agent CLI instead of calling an API, reusing the
founder's saved CLI login (ChatGPT subscription for ``codex``, whatever
``opencode auth login`` stored for ``opencode``) — so no API key is
required. Model ids follow the same ``<cli>:<model>`` convention as
``claude-code:sonnet``:

* ``codex:gpt-5`` → ``codex exec -m gpt-5`` (headless JSONL one-shot)
* ``opencode:openai/gpt-5`` → ``opencode run -m openai/gpt-5`` (the
  suffix is opencode's native ``provider/model`` form, passed through)

These are TEXT-COMPLETION adapters for L2 single-shot calls (issue #18)
— the minimal no-tools invocation per CLI. Multi-turn agentic execution
lives in ``core/harness/`` and is out of scope here.

This module owns only the subprocess mechanics. Cost recording stays in
:class:`kompany.llm.client.LLMClient` (single entry point — see
the internal design spec). Flag/shape sources: research doc
the internal design spec
(codex 0.133.0 / opencode 1.1.42, verified 2026-06-11).
"""

from __future__ import annotations

import json
import os
import subprocess

DEFAULT_TIMEOUT_SECONDS = 300.0

# Hygiene: opencode loads the founder's ~/.claude/CLAUDE.md and
# .claude/skills by default (same leak class as Claude Code's setting
# sources — personas/output styles break strict-JSON parsing upstream).
# These env vars are the documented kill switches; OPENCODE_PERMISSION
# pre-resolves every tool to deny so a single-shot text completion can't
# wander into bash/edits (subprocess mode has no permission responder).
# Local copy by design: the harness leg's scrub helpers live under
# core/harness/ and importing them here would drag harness deps into the
# llm/ layer.
_OPENCODE_ENV: dict[str, str] = {
    "OPENCODE_DISABLE_CLAUDE_CODE": "true",
    "OPENCODE_DISABLE_AUTOUPDATE": "true",
    "OPENCODE_PERMISSION": json.dumps({
        "read": "deny",
        "edit": "deny",
        "bash": "deny",
        "glob": "deny",
        "grep": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "task": "deny",
        "skill": "deny",
        "external_directory": "deny",
    }),
}


def run_cli_completion(
    cli: str, model: str, system: str, prompt: str, timeout: float
) -> tuple[str, int, int]:
    """Run one single-shot completion through a local agent CLI.

    ``cli`` is ``"codex"`` or ``"opencode"``. Returns
    ``(text, input_tokens, output_tokens)``. Raises :class:`RuntimeError`
    with an actionable message when the binary is missing, exits
    non-zero, times out, or emits unparseable output — mirroring
    :func:`kompany.llm.claude_code.run_claude_code` so the existing
    provider-error handling in ``LLMClient`` wraps it identically.
    """
    if cli == "codex":
        return _run_codex(model, system, prompt, timeout)
    if cli == "opencode":
        return _run_opencode(model, system, prompt, timeout)
    raise RuntimeError(f"unknown CLI provider {cli!r}")


def _run_subprocess(
    cmd: list[str],
    *,
    cli: str,
    cli_model: str,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Shared subprocess mechanics + the claude_code error contract."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"{cli} CLI not found on PATH — install it or switch to an "
            "API-key provider"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{cli} CLI timed out after {timeout:.0f}s (model={cli_model})"
        ) from exc
    if proc.returncode != 0:
        stderr_tail = (proc.stderr or "").strip()[-500:]
        raise RuntimeError(
            f"{cli} CLI exited {proc.returncode} "
            f"(model={cli_model}): {stderr_tail or '(no stderr)'}"
        )
    return proc


def _combined_prompt(system: str, prompt: str) -> str:
    """Fold the system prompt into the user prompt.

    Neither ``codex exec`` nor ``opencode run`` exposes a system-prompt
    flag in one-shot subprocess mode, so the system text rides as a
    prefixed instruction block instead.
    """
    if not system:
        return prompt
    return f"{system}\n\n{prompt}"


def _iter_jsonl(stdout: str):
    """Yield parsed JSON objects from a JSONL stream, skipping noise."""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            yield obj


# ---------------------------------------------------------------------------
# codex exec
# ---------------------------------------------------------------------------


def _run_codex(
    model: str, system: str, prompt: str, timeout: float
) -> tuple[str, int, int]:
    """One headless ``codex exec`` call (JSONL on stdout).

    Flags (codex 0.133.0, research doc):

    * ``--json`` — stdout becomes the JSONL event stream (without it,
      progress goes to stderr and only the final message to stdout).
    * ``--sandbox read-only`` — single-shot text completion must not
      touch the filesystem; the OS sandbox enforces it.
    * ``--skip-git-repo-check`` — codex refuses non-git cwds by default;
      L2 calls run from wherever the engine lives.
    * ``--ignore-user-config`` / ``--ignore-rules`` — don't load the
      founder's ``~/.codex/config.toml`` / execpolicy rules (same
      isolation discipline as claude_code's ``--setting-sources ""``).
    """
    cli_model = model.split(":", 1)[1] if ":" in model else "gpt-5"
    cmd = [
        "codex",
        "exec",
        "--json",
        "--model",
        cli_model,
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        _combined_prompt(system, prompt),
    ]
    proc = _run_subprocess(cmd, cli="codex", cli_model=cli_model, timeout=timeout)

    text = ""
    tokens_in = 0
    tokens_out = 0
    error_detail = ""
    for obj in _iter_jsonl(proc.stdout):
        kind = obj.get("type", "")
        if kind == "item.completed":
            item = obj.get("item") or {}
            item_type = item.get("type") or item.get("item_type")
            if item_type == "agent_message":
                # Last agent message wins — it's the final reply.
                text = str(item.get("text", "") or text)
        elif kind == "turn.completed":
            usage = obj.get("usage") or {}
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
        elif kind in ("turn.failed", "error"):
            error_detail = json.dumps(obj)[:200]
    if not text:
        stderr_tail = (proc.stderr or "").strip()[-200:]
        raise RuntimeError(
            f"codex CLI returned no completion text (model={cli_model}): "
            f"{error_detail or (proc.stdout or '').strip()[:200] or stderr_tail}"
        )
    return text, tokens_in, tokens_out


# ---------------------------------------------------------------------------
# opencode run
# ---------------------------------------------------------------------------


def _run_opencode(
    model: str, system: str, prompt: str, timeout: float
) -> tuple[str, int, int]:
    """One ``opencode run --format json`` call (JSONL events on stdout).

    The model suffix is opencode's native ``provider/model`` form
    (``opencode:openai/gpt-5`` → ``-m openai/gpt-5``); omitted suffix
    lets opencode pick its configured default. Events (run.ts, 1.1.42):
    ``text`` parts carry the reply, ``step_finish`` carries
    ``tokens.input/.output`` (and per-step USD cost — unused here;
    pricing stays in ``llm/models.py`` like every provider).
    """
    cli_model = model.split(":", 1)[1] if ":" in model else ""
    cmd = ["opencode", "run", _combined_prompt(system, prompt), "--format", "json"]
    if cli_model:
        cmd += ["--model", cli_model]
    env = {**os.environ, **_OPENCODE_ENV}
    proc = _run_subprocess(
        cmd,
        cli="opencode",
        cli_model=cli_model or "(default)",
        timeout=timeout,
        env=env,
    )

    parts: list[str] = []
    tokens_in = 0
    tokens_out = 0
    error_detail = ""
    for obj in _iter_jsonl(proc.stdout):
        kind = obj.get("type", "")
        part = obj.get("part") or {}
        if kind == "text":
            chunk = part.get("text") or obj.get("text") or ""
            if chunk:
                parts.append(str(chunk))
        elif kind == "step_finish":
            tokens = part.get("tokens") or obj.get("tokens") or {}
            tokens_in += int(tokens.get("input") or 0)
            tokens_out += int(tokens.get("output") or 0)
        elif kind == "error":
            error_detail = json.dumps(obj)[:200]
    text = "\n".join(parts).strip()
    if not text:
        stderr_tail = (proc.stderr or "").strip()[-200:]
        raise RuntimeError(
            "opencode CLI returned no completion text "
            f"(model={cli_model or '(default)'}): "
            f"{error_detail or (proc.stdout or '').strip()[:200] or stderr_tail}"
        )
    return text, tokens_in, tokens_out
