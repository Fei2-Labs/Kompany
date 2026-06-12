"""Shared fake-subprocess stubs for harness vehicle tests.

No real CLI runs, no spend: a FakePopen replays canned JSONL events.
Mirrors the stub pattern in ``test_harness_claude_runner.py``.
"""

from __future__ import annotations

import io
import json
import time
from typing import Any


class BlockingStdout:
    """Stdout that never yields a line until the process is terminated."""

    def __init__(self) -> None:
        self.proc: FakePopen | None = None

    def __iter__(self):
        return self

    def __next__(self) -> str:
        deadline = time.time() + 5.0  # safety net so tests can't hang
        while time.time() < deadline:
            if self.proc is not None and (self.proc.terminated or self.proc.killed):
                raise StopIteration
            time.sleep(0.01)
        raise StopIteration


class FakePopen:
    def __init__(
        self,
        args,
        *,
        events=(),
        returncode=0,
        stderr_text="",
        block=False,
    ) -> None:
        self.args = args
        self.returncode = returncode
        # Way above macOS/Linux pid_max so os.getpgid raises and the
        # runner falls back to .terminate() on this stub.
        self.pid = 999_999_999
        self.terminated = False
        self.killed = False
        self.stderr = io.StringIO(stderr_text)
        if block:
            stdout = BlockingStdout()
            stdout.proc = self
            self.stdout = stdout
        else:
            self.stdout = [json.dumps(e) + "\n" for e in events]

    def wait(self, timeout=None) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    # Context-manager + communicate support so incidental subprocess.run
    # calls (e.g. git_files_changed in a fake .git workspace) don't blow
    # up while the stub is installed on the global subprocess module.
    def __enter__(self) -> "FakePopen":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def communicate(self, input=None, timeout=None) -> tuple[str, str]:
        return ("", "")

    def poll(self) -> int:
        return self.returncode


def install_fake_popen(
    monkeypatch,
    module,
    events=(),
    returncode=0,
    stderr_text="",
    block=False,
) -> dict[str, Any]:
    """Install a Popen stub on ``module.subprocess``; returns captured state."""
    state: dict[str, Any] = {}

    def popen(cmd, **kwargs):
        proc = FakePopen(
            cmd,
            events=events,
            returncode=returncode,
            stderr_text=stderr_text,
            block=block,
        )
        if cmd and cmd[0] != "git":  # don't clobber state on diff-evidence calls
            state["cmd"] = cmd
            state["kwargs"] = kwargs
            state["proc"] = proc
        return proc

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    return state
