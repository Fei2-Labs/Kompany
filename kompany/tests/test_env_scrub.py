"""Secrets never reach LLM-controlled subprocesses through the environment."""

from __future__ import annotations

import subprocess

from kompany.core.harness import native_tools
from kompany.core.harness.env_scrub import scrubbed_env


def test_scrubbed_env_drops_secret_like_names_keeps_plain():
    base = {
        "PATH": "/usr/bin", "HOME": "/h", "LANG": "C", "ANTHROPIC_API_KEY": "sk", "OPENAI_API_KEY": "sk",
        "KOMPANY_VAULT_KEY": "v", "KOMPANY_DATA_DIR": "/d", "MY_APP_TOKEN": "t", "DB_PASSWORD": "p",
        "AWS_SECRET_ACCESS_KEY": "a", "GITHUB_TOKEN": "g", "SSH_AUTH_SOCK": "/s", "SOME_COOKIE": "c",
        "TERM": "xterm",
    }
    out = scrubbed_env(base)
    assert set(out) == {"PATH", "HOME", "LANG", "SSH_AUTH_SOCK", "TERM"}
    assert scrubbed_env(base, keep=frozenset({"MY_APP_TOKEN"}))["MY_APP_TOKEN"] == "t"


def test_run_shell_does_not_inherit_api_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak")
    monkeypatch.setenv("KOMPANY_VAULT_KEY", "vault-leak")
    monkeypatch.setenv("HARMLESS_VAR", "fine")
    captured = {}

    def fake_run(argv, **kw):
        captured.update(kw)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(native_tools.subprocess, "run", fake_run)
    native_tools._run_shell(tmp_path, {"command": "env"})
    env = captured["env"]
    assert "ANTHROPIC_API_KEY" not in env and "KOMPANY_VAULT_KEY" not in env
    assert env["HARMLESS_VAR"] == "fine" and "PATH" in env
