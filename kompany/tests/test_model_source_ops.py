"""ModelSource founder surfaces — engine ops + four-interface parity.

06-11-harness-execution-leg PR5b. Covers the engine get/set/clear
roundtrip with YAML persistence, validation error surfacing, audit
hygiene (no auth/pricing material in the audit detail), agent-CLI
detection, and the REST / MCP / SDK / CLI parity contract from
``.trellis/spec/agents/interfaces.md``.
"""

from __future__ import annotations

import json

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kompany.config.settings import KompanySettings
from kompany.core import model_source_ops
from kompany.interfaces.api import app as api_app
from kompany.interfaces.cli import app as cli_app
from kompany.state.audit import AuditLog
from kompany.state.database import Database


class OpsEngine:
    """Duck-typed engine carrying exactly what model_source_ops needs.

    Mirrors ``KompanyEngine``'s thin wrappers so the same instance can
    stand in behind REST/MCP/SDK/CLI in the parity tests below.
    """

    def __init__(self, tmp_path, config_path=None):
        self.db = Database(tmp_path)
        self.audit = AuditLog(self.db)
        self.settings = KompanySettings(data_dir=tmp_path)
        self._config_path = config_path

    def get_model_source(self):
        return model_source_ops.get_model_source(self)

    def set_model_source(self, payload):
        return model_source_ops.set_model_source(self, payload)

    def detect_agent_clis(self):
        return model_source_ops.detect_agent_clis()


SUB_PAYLOAD = {"kind": "claude_subscription", "monthly_fee_usd": 20.0}


# ---------------------------------------------------------------------------
# Engine ops — roundtrip, persistence, validation, audit hygiene
# ---------------------------------------------------------------------------


def test_get_returns_none_when_unconfigured(tmp_path):
    assert OpsEngine(tmp_path).get_model_source() is None


def test_set_get_clear_roundtrip_and_yaml_persistence(tmp_path):
    eng = OpsEngine(tmp_path)
    result = eng.set_model_source(SUB_PAYLOAD)
    source = result["source"]
    assert source["kind"] == "claude_subscription"
    assert source["billing_mode"] == "subscription"
    assert source["monthly_fee_usd"] == 20.0
    # Derived, read-only info — never an input.
    assert source["vehicle"] == "claude_code"
    assert "runner" not in source["execution_summary"].lower()
    assert eng.get_model_source() == source

    # Persisted to <data_dir>/config.yaml; KompanySettings.load reads it
    # back on a bare boot (config_path=None + KOMPANY_DATA_DIR).
    cfg = tmp_path / "config.yaml"
    data = yaml.safe_load(cfg.read_text())
    assert data["model_source"]["kind"] == "claude_subscription"

    # Clear → legacy path; the YAML key is removed, not tombstoned.
    cleared = eng.set_model_source(None)
    assert cleared == {"source": None}
    assert eng.get_model_source() is None
    assert eng.settings.model_source is None
    assert "model_source" not in (yaml.safe_load(cfg.read_text()) or {})


def test_settings_load_falls_back_to_data_dir_config(tmp_path, monkeypatch):
    eng = OpsEngine(tmp_path)
    eng.set_model_source(SUB_PAYLOAD)
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    settings = KompanySettings.load(None)
    assert settings.model_source is not None
    assert settings.model_source.kind == "claude_subscription"
    assert settings.model_source.vehicle == "claude_code"


def test_explicit_config_path_wins_for_persistence(tmp_path):
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("company:\n  name: TestCo\n")
    eng = OpsEngine(tmp_path, config_path=str(explicit))
    eng.set_model_source({"kind": "custom_api"})
    data = yaml.safe_load(explicit.read_text())
    assert data["model_source"]["kind"] == "custom_api"
    assert data["company"]["name"] == "TestCo"  # other keys preserved
    # The explicit path is what a load(config_path=...) boot reads back.
    settings = KompanySettings.load(str(explicit))
    assert settings.model_source is not None
    assert settings.model_source.kind == "custom_api"
    # And the data-dir default was never touched.
    assert not (tmp_path / "config.yaml").exists()


def test_validation_error_surfaces_and_nothing_persists(tmp_path):
    eng = OpsEngine(tmp_path)
    with pytest.raises(ValueError, match="monthly_fee_usd"):
        eng.set_model_source({"kind": "claude_subscription"})  # fee missing
    with pytest.raises(ValueError):
        eng.set_model_source({"kind": "kiro_subscription", "monthly_fee_usd": 9.0})
    assert eng.get_model_source() is None
    assert not (tmp_path / "config.yaml").exists()
    # No audit entry for a failed set.
    events = [e["event_type"] for e in eng.audit.recent(limit=10)]
    assert model_source_ops.AUDIT_EVENT not in events


def test_audit_detail_carries_kind_only_never_pricing(tmp_path):
    eng = OpsEngine(tmp_path)
    eng.set_model_source(
        {
            "kind": "claude_subscription",
            "monthly_fee_usd": 123.45,
            "price_overrides": {"secret-private-model": (1.0, 2.0)},
        }
    )
    eng.set_model_source(None)
    rows = [
        e for e in eng.audit.recent(limit=10)
        if e["event_type"] == model_source_ops.AUDIT_EVENT
    ]
    assert len(rows) == 2
    clear_detail = json.loads(rows[0]["detail"])  # newest first
    set_detail = json.loads(rows[1]["detail"])
    assert set_detail == {"kind": "claude_subscription", "billing_mode": "subscription"}
    assert clear_detail == {"cleared": True}
    for row in rows:
        assert "secret-private-model" not in row["detail"]
        assert "123.45" not in row["detail"]


# ---------------------------------------------------------------------------
# detect_agent_clis
# ---------------------------------------------------------------------------


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_detect_clis_found_missing_and_version(tmp_path):
    def fake_which(name):
        return {"claude": "/usr/local/bin/claude", "opencode": "/opt/oc"}.get(name)

    def fake_run(cmd, **kwargs):
        assert kwargs["timeout"] == model_source_ops.VERSION_PROBE_TIMEOUT
        if cmd[0] == "claude":
            return _Proc(stdout="2.1.173 (Claude Code)\n")
        raise OSError("exec format error")  # version probe must not crash

    clis = model_source_ops.detect_agent_clis(which=fake_which, run=fake_run)
    assert clis["claude"] == {
        "found": True,
        "path": "/usr/local/bin/claude",
        "version": "2.1.173 (Claude Code)",
        "source_kind": "claude_subscription",
    }
    assert clis["codex"]["found"] is False
    assert clis["codex"]["version"] is None
    assert clis["codex"]["source_kind"] == "openai_subscription"
    assert clis["opencode"]["found"] is True
    assert clis["opencode"]["version"] is None  # best-effort probe failed
    assert clis["opencode"]["source_kind"] == "custom_api"


def test_detect_clis_ignores_nonzero_version_exit(tmp_path):
    clis = model_source_ops.detect_agent_clis(
        which=lambda name: "/bin/x" if name == "codex" else None,
        run=lambda cmd, **kw: _Proc(returncode=1, stderr="boom"),
    )
    assert clis["codex"]["found"] is True
    assert clis["codex"]["version"] is None


# ---------------------------------------------------------------------------
# REST parity
# ---------------------------------------------------------------------------

client = TestClient(api_app)
runner = CliRunner()


def test_rest_model_source_roundtrip(tmp_path, monkeypatch):
    eng = OpsEngine(tmp_path)
    monkeypatch.setattr("kompany.interfaces.api._engine", eng)

    assert client.get("/settings/model-source").json() is None

    res = client.put("/settings/model-source", json=SUB_PAYLOAD)
    assert res.status_code == 200
    assert res.json()["source"]["kind"] == "claude_subscription"
    assert res.json()["source"]["vehicle"] == "claude_code"

    # GET reflects the same dict shape the engine/SDK emit.
    assert client.get("/settings/model-source").json() == eng.get_model_source()

    # kind omitted (or null) clears back to legacy billing.
    res = client.put("/settings/model-source", json={})
    assert res.status_code == 200
    assert res.json() == {"source": None}
    assert client.get("/settings/model-source").json() is None


def test_rest_model_source_validation_is_400(tmp_path, monkeypatch):
    eng = OpsEngine(tmp_path)
    monkeypatch.setattr("kompany.interfaces.api._engine", eng)
    res = client.put(
        "/settings/model-source", json={"kind": "claude_subscription"}
    )
    assert res.status_code == 400
    assert "monthly_fee_usd" in res.json()["detail"]


def test_rest_detect_clis(tmp_path, monkeypatch):
    eng = OpsEngine(tmp_path)
    eng.detect_agent_clis = lambda: {"claude": {"found": True}}  # type: ignore[method-assign]
    monkeypatch.setattr("kompany.interfaces.api._engine", eng)
    assert client.get("/settings/detect-clis").json() == {"claude": {"found": True}}


# ---------------------------------------------------------------------------
# MCP parity — shared dispatch + tool registration
# ---------------------------------------------------------------------------


def test_mcp_dispatch_show_set_clear_detect(tmp_path):
    from kompany.interfaces.mcp_dispatch import dispatcher

    eng = OpsEngine(tmp_path)
    assert dispatcher.dispatch_tool(eng, "kompany_model_source_show", {}) is None

    result = dispatcher.dispatch_tool(
        eng, "kompany_model_source_set", dict(SUB_PAYLOAD)
    )
    assert result["source"]["kind"] == "claude_subscription"
    # Show now matches the REST/SDK shape exactly.
    shown = dispatcher.dispatch_tool(eng, "kompany_model_source_show", {})
    assert shown == eng.get_model_source()

    # Validation error → {"error": ...}, MCP-style (no exception).
    bad = dispatcher.dispatch_tool(
        eng, "kompany_model_source_set", {"kind": "claude_subscription"}
    )
    assert "monthly_fee_usd" in bad["error"]
    # Bare call (no kind, no clear) never silently clears.
    noop = dispatcher.dispatch_tool(eng, "kompany_model_source_set", {})
    assert "error" in noop
    assert eng.get_model_source() is not None

    cleared = dispatcher.dispatch_tool(
        eng, "kompany_model_source_set", {"clear": True}
    )
    assert cleared == {"source": None}

    eng.detect_agent_clis = lambda: {"codex": {"found": False}}  # type: ignore[method-assign]
    assert dispatcher.dispatch_tool(eng, "kompany_detect_clis", {}) == {
        "codex": {"found": False}
    }


def test_mcp_tools_registered():
    mcp = __import__("importlib").import_module("kompany.interfaces.mcp_server")
    names = [tool.name for tool in mcp.TOOLS]
    for tool_name in (
        "kompany_model_source_show",
        "kompany_model_source_set",
        "kompany_detect_clis",
    ):
        assert tool_name in names


# ---------------------------------------------------------------------------
# SDK parity
# ---------------------------------------------------------------------------


def test_sdk_dict_shapes(tmp_path, monkeypatch):
    from kompany.interfaces.sdk import Kompany

    eng = OpsEngine(tmp_path)
    monkeypatch.setattr(
        "kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: eng
    )
    k = Kompany()
    assert k.model_source() is None
    result = k.set_model_source(SUB_PAYLOAD)
    assert isinstance(result, dict)
    assert result["source"]["billing_mode"] == "subscription"
    assert k.model_source() == result["source"]
    assert k.set_model_source(None) == {"source": None}

    eng.detect_agent_clis = lambda: {"opencode": {"found": True}}  # type: ignore[method-assign]
    assert k.detect_clis() == {"opencode": {"found": True}}


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_model_source_set_show_detect(tmp_path, monkeypatch):
    eng = OpsEngine(tmp_path)
    monkeypatch.setattr(
        "kompany.interfaces.cli._get_engine", lambda config=None: eng
    )

    result = runner.invoke(
        cli_app,
        ["model-source", "set", "--kind", "claude_subscription", "--monthly-fee", "20"],
    )
    assert result.exit_code == 0
    assert "claude_subscription" in result.output

    result = runner.invoke(cli_app, ["model-source", "show", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["kind"] == "claude_subscription"
    # Founder never sees the word "runner" on this surface.
    assert "runner" not in result.output.lower()

    eng.detect_agent_clis = lambda: {  # type: ignore[method-assign]
        "claude": {
            "found": True, "path": "/x", "version": "2.1.173",
            "source_kind": "claude_subscription",
        }
    }
    result = runner.invoke(cli_app, ["model-source", "detect"])
    assert result.exit_code == 0
    assert "claude" in result.output

    result = runner.invoke(cli_app, ["model-source", "set", "--clear"])
    assert result.exit_code == 0
    assert eng.get_model_source() is None


def test_cli_model_source_set_validation_exits_nonzero(tmp_path, monkeypatch):
    eng = OpsEngine(tmp_path)
    monkeypatch.setattr(
        "kompany.interfaces.cli._get_engine", lambda config=None: eng
    )
    result = runner.invoke(
        cli_app, ["model-source", "set", "--kind", "claude_subscription"]
    )
    assert result.exit_code == 1
    # No kind and no --clear is an error too, never a silent clear.
    result = runner.invoke(cli_app, ["model-source", "set"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Real engine — thin wrappers + restart persistence
# ---------------------------------------------------------------------------


def test_real_engine_roundtrip_survives_restart(tmp_path, monkeypatch):
    from kompany.core.engine import KompanyEngine

    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    engine = KompanyEngine()
    assert engine.get_model_source() is None
    engine.set_model_source({"kind": "openai_subscription", "monthly_fee_usd": 30.0})
    assert engine.settings.model_source is not None
    assert engine.settings.model_source.vehicle == "codex"

    # A fresh engine boot (same data dir, no explicit config) reads the
    # persisted source back — the Settings change survives restarts.
    reborn = KompanyEngine()
    source = reborn.get_model_source()
    assert source is not None
    assert source["kind"] == "openai_subscription"
    assert source["monthly_fee_usd"] == 30.0
