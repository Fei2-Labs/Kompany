"""Shared subprocess/stream plumbing for loop-vehicle adapters.

Every vehicle drives a CLI the same way: spawn it in its own process
group inside the workspace, iterate JSONL events off stdout, drain stderr
on a side thread, enforce a wall-clock timeout, and escalate
SIGTERM→SIGKILL on shutdown. Extracted here so claude/opencode/codex
adapters share one battle-tested loop instead of three copies.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable

from kompany.core.harness.protocol import (
    EventCallback,
    EventKind,
    HandoffBrief,
    HarnessEvent,
    HarnessResult,
)

DEFAULT_TIMEOUT_SECONDS = 3600.0

# Return False from an event handler to abort the run (kills the process
# group) — used for external budget enforcement (research pitfall 9).
EventHandler = Callable[[dict[str, Any]], bool]


class RunAbort(Exception):
    """Raised by an ``on_event`` consumer to kill the in-flight run.

    The vehicle-internal handler-returns-False channel covers aborts the
    *runner* decides on (budget meter breach); orchestrator-side caps
    (external turn-cap enforcement in ``core/harness_execution``, PRD D3)
    need an abort channel *through* ``on_event``. ``emit_event`` re-raises
    this one exception (every other consumer error is swallowed), which
    makes ``run_streaming`` terminate the process group before
    propagating — so no CLI ever keeps spending unsupervised.
    """


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Copy of os.environ minus CLAUDE*/OPENCODE* keys (PRD safety shell).

    Covers CLAUDE_* plus CLAUDECODE=1 (research pitfall: an inherited env
    can silently flip permissionMode to bypassPermissions; opencode also
    reads Claude artifacts) and OPENCODE_* (an inherited
    OPENCODE_CONFIG_CONTENT/OPENCODE_PERMISSION would override the per-run
    restrictive profile). ANTHROPIC_API_KEY stays as-is. ``extra`` entries
    are layered on top (controlled per-run injection, e.g.
    OPENCODE_PERMISSION).
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("CLAUDE", "OPENCODE"))
    }
    if extra:
        env.update(extra)
    return env


def terminate_process_group(
    proc: subprocess.Popen[str], sig: int = signal.SIGTERM
) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except OSError:
        try:
            if sig == signal.SIGKILL:
                proc.kill()
            else:
                proc.terminate()
        except OSError:
            pass


def drain_stderr(stream: IO[str], chunks: list[str]) -> None:
    try:
        chunks.append(stream.read() or "")
    except (OSError, ValueError):
        pass


def emit_event(
    on_event: EventCallback | None,
    kind: EventKind,
    payload: dict[str, Any],
    raw: dict[str, Any] | None = None,
) -> None:
    """Deliver one normalized event; consumer exceptions never abort a run.

    Exception: :class:`RunAbort` is the consumer's deliberate kill switch
    (external cap enforcement) and propagates — ``run_streaming``'s
    handler-raise path then terminates the process group.
    """
    if on_event is None:
        return
    try:
        on_event(HarnessEvent(kind=kind, payload=payload, raw=raw))
    except RunAbort:
        raise
    except Exception:  # noqa: BLE001 — consumer bug must not kill the session
        pass


@dataclass
class StreamOutcome:
    """How a streamed CLI run ended (process-level, vehicle-agnostic)."""

    timed_out: bool
    aborted: bool  # the event handler requested the kill (e.g. budget cap)
    returncode: int | None
    stderr_tail: str


def run_streaming(
    cmd: list[str],
    workspace: Path,
    env: dict[str, str],
    timeout_seconds: float,
    handle_event: EventHandler,
    missing_binary_hint: str,
) -> StreamOutcome:
    """Spawn ``cmd`` in ``workspace`` and feed each JSONL stdout event to
    ``handle_event``; a False return kills the process group mid-run."""
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workspace),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(missing_binary_hint) from exc

    timed_out = threading.Event()

    def _kill_on_timeout() -> None:
        timed_out.set()
        terminate_process_group(proc)

    timer = threading.Timer(timeout_seconds, _kill_on_timeout)
    timer.daemon = True
    timer.start()

    stderr_chunks: list[str] = []
    stderr_thread: threading.Thread | None = None
    if proc.stderr is not None:
        stderr_thread = threading.Thread(
            target=drain_stderr, args=(proc.stderr, stderr_chunks), daemon=True
        )
        stderr_thread.start()

    aborted = False
    stream_done = False
    try:
        for raw_line in proc.stdout or []:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if not handle_event(event):
                aborted = True
                terminate_process_group(proc)
                break
        stream_done = True
    finally:
        timer.cancel()
        if not stream_done:
            # The event handler raised — terminate before re-raising so a
            # live CLI never keeps running (and spending) unsupervised.
            terminate_process_group(proc)
        try:
            proc.wait(timeout=30.0)
        except subprocess.TimeoutExpired:
            # SIGTERM was ignored — escalate so no zombie process lingers
            # holding the workspace.
            terminate_process_group(proc, signal.SIGKILL)
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass
        if stderr_thread is not None:
            # Don't race the drain thread for the stderr tail.
            stderr_thread.join(timeout=5.0)
    return StreamOutcome(
        timed_out=timed_out.is_set(),
        aborted=aborted,
        returncode=proc.returncode,
        stderr_tail="".join(stderr_chunks).strip()[-500:],
    )


def build_handoff_brief(result: HarnessResult, vehicle: str) -> HandoffBrief:
    """Assemble a portability brief from result fields (non-LLM, PRD D4)."""
    done = f"Run finished with exit_status={result.exit_status}."
    if result.files_changed:
        done += f" Files changed: {', '.join(result.files_changed)}."
    else:
        done += " No files changed."
    if result.final_text:
        done += f"\nFinal output:\n{result.final_text}"

    stuck_parts: list[str] = []
    if result.error:
        stuck_parts.append(f"Error: {result.error}")
    if result.permission_denials:
        denied = ", ".join(
            sorted(
                {str(d.get("tool_name", "?")) for d in result.permission_denials}
            )
        )
        stuck_parts.append(f"Permission denied for: {denied}")
    stuck = "\n".join(stuck_parts) or "Nothing blocked."

    if result.permission_denials:
        next_steps = (
            "Re-run with the denied tools approved (route them through "
            "the approval inbox), then resume the session."
        )
    elif result.exit_status == "budget_exceeded":
        next_steps = (
            "Budget cap reached — get the spend approved (or raise the "
            "cap), then resume the session from where it stopped."
        )
    elif result.exit_status == "error_max_turns":
        next_steps = (
            "Turn cap reached — resume the session with a higher cap "
            "to continue from where it stopped."
        )
    elif result.exit_status == "timeout":
        next_steps = (
            "Wall-clock timeout — inspect the workspace diff, then "
            "resume the session. Reported cost is non-authoritative."
        )
    elif result.error:
        next_steps = (
            "Inspect the error and workspace state, then retry or "
            "resume the session."
        )
    else:
        next_steps = (
            "Review the final output and workspace diff; continue from "
            "the workspace state."
        )
    return HandoffBrief(
        done=done,
        stuck=stuck,
        next_steps=next_steps,
        session_id=result.session_id,
        vehicle=vehicle,
    )
