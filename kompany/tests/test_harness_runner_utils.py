"""Tests for runner_utils — shared subprocess/stream plumbing.

Covers the process-lifecycle guarantees every vehicle relies on: no leaked
CLI process on handler exceptions, SIGTERM→SIGKILL escalation, env scrub,
and the missing-binary hint. No real CLI runs.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from kompany.core.harness import runner_utils
from tests.harness_stubs import FakePopen

EVENTS = ({"type": "one"}, {"type": "two"})


def _install(monkeypatch, proc: FakePopen) -> None:
    monkeypatch.setattr(
        runner_utils.subprocess, "Popen", lambda cmd, **kwargs: proc
    )


def _run(proc_handler, tmp_path) -> runner_utils.StreamOutcome:
    return runner_utils.run_streaming(
        cmd=["fake-cli"],
        workspace=tmp_path,
        env={},
        timeout_seconds=5.0,
        handle_event=proc_handler,
        missing_binary_hint="fake-cli not found — install fake-cli",
    )


def test_handler_exception_does_not_leak_the_process(monkeypatch, tmp_path):
    proc = FakePopen(["fake-cli"], events=EVENTS)
    _install(monkeypatch, proc)

    def explode(event: dict[str, Any]) -> bool:
        raise RuntimeError("handler bug")

    with pytest.raises(RuntimeError, match="handler bug"):
        _run(explode, tmp_path)
    # A live CLI must never keep running (and spending) unsupervised.
    assert proc.terminated


class _StubbornPopen(FakePopen):
    """Ignores SIGTERM: the first wait() after spawn times out."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.wait_calls = 0

    def wait(self, timeout=None) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise subprocess.TimeoutExpired(
                cmd=self.args, timeout=timeout or 0.0
            )
        return self.returncode


def test_sigterm_ignored_escalates_to_sigkill(monkeypatch, tmp_path):
    proc = _StubbornPopen(["fake-cli"], events=EVENTS)
    _install(monkeypatch, proc)
    outcome = _run(lambda event: False, tmp_path)  # abort on first event
    assert outcome.aborted
    assert proc.terminated  # SIGTERM first
    assert proc.killed  # escalated when SIGTERM was ignored — no zombie
    assert proc.wait_calls >= 2


def test_missing_binary_raises_actionable_hint(monkeypatch, tmp_path):
    def raise_fnf(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(runner_utils.subprocess, "Popen", raise_fnf)
    with pytest.raises(RuntimeError, match="install fake-cli"):
        _run(lambda event: True, tmp_path)


def test_scrubbed_env_drops_claude_and_opencode_prefixes(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_FOO", "leak")
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    env = runner_utils.scrubbed_env(extra={"OPENCODE_PERMISSION": "{}"})
    assert "CLAUDECODE" not in env
    assert "CLAUDE_FOO" not in env
    assert "OPENCODE_CONFIG_CONTENT" not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    # Controlled per-run injection layers on top of the scrub.
    assert env["OPENCODE_PERMISSION"] == "{}"
