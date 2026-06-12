"""Workspace registry — multi-brand isolation (issue #15).

Covers: registry CRUD + default-seed migration, env bypass, settings
resolution order (env > YAML data_dir > active workspace > default),
REST switch rebinding the cached engine, create mkdir+register,
remove keeping data on disk, and CLI/REST/MCP/SDK parity.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from kompany.config import workspaces
from kompany.config.settings import KompanySettings

runner = CliRunner()


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    """Point the registry + workspaces root at tmp; clear the data-dir env
    (the conftest autouse fixture sets KOMPANY_DATA_DIR for isolation —
    these tests exercise the registry path, which env would bypass)."""
    reg = tmp_path / "workspaces.json"
    monkeypatch.setenv("KOMPANY_WORKSPACES_FILE", str(reg))
    monkeypatch.setenv("KOMPANY_WORKSPACES_ROOT", str(tmp_path / "ws-root"))
    monkeypatch.delenv("KOMPANY_DATA_DIR", raising=False)
    return reg


# ----- registry CRUD + seed migration --------------------------------------


def test_missing_registry_seeds_default(registry):
    data = workspaces.load_registry()
    assert data["active"] == "default"
    assert data["workspaces"]["default"]["data_dir"] == "~/.kompany"
    # Read-only: the seed is in-memory, no file written on load.
    assert not registry.exists()


def test_corrupt_registry_falls_back_to_seed(registry):
    registry.write_text("{not json")
    assert workspaces.load_registry()["active"] == "default"
    registry.write_text(json.dumps({"active": "x", "workspaces": {}}))
    assert workspaces.load_registry()["active"] == "default"


def test_register_set_active_remove_roundtrip(registry, tmp_path):
    workspaces.register("brand-a", tmp_path / "a", label="Brand A")
    entry = workspaces.set_active("brand-a")
    assert entry["active"] is True
    assert registry.exists()

    payload = workspaces.workspaces_list()
    assert payload["active"] == "brand-a"
    names = {w["name"] for w in payload["workspaces"]}
    assert names == {"default", "brand-a"}

    # Remove keeps data on disk — entry-only.
    data_dir = tmp_path / "a"
    data_dir.mkdir()
    (data_dir / "kompany.db").write_text("precious")
    workspaces.set_active("default")
    removed = workspaces.remove("brand-a")
    assert removed["data_kept"] is True
    assert (data_dir / "kompany.db").read_text() == "precious"
    assert "brand-a" not in {
        w["name"] for w in workspaces.workspaces_list()["workspaces"]
    }


def test_remove_active_workspace_rejected(registry):
    with pytest.raises(workspaces.WorkspaceError):
        workspaces.remove("default")


def test_register_duplicate_and_bad_name_rejected(registry, tmp_path):
    workspaces.register("brand-a", tmp_path / "a")
    with pytest.raises(workspaces.WorkspaceError):
        workspaces.register("brand-a", tmp_path / "other")
    with pytest.raises(workspaces.WorkspaceError):
        workspaces.register("Bad Name!", tmp_path / "x")
    with pytest.raises(workspaces.WorkspaceError):
        workspaces.set_active("nope")


def test_create_makes_dir_and_registers(registry, tmp_path):
    entry = workspaces.create("brand-b", label="Brand B")
    target = tmp_path / "ws-root" / "brand-b"
    assert target.is_dir()
    assert entry["data_dir"] == str(target)
    assert entry["active"] is False  # create never auto-switches
    assert workspaces.workspaces_list()["active"] == "default"


# ----- env bypass -----------------------------------------------------------


def test_env_data_dir_bypasses_registry(registry, tmp_path, monkeypatch):
    workspaces.register("brand-a", tmp_path / "a")
    workspaces.set_active("brand-a")
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "pinned"))

    assert workspaces.active_data_dir() is None
    assert workspaces.resolve_data_dir() == tmp_path / "pinned"
    assert workspaces.workspaces_list()["env_override"] is True


def test_resolve_data_dir_chain(registry, tmp_path):
    # No env, default registry → ~/.kompany.
    assert workspaces.resolve_data_dir() == Path("~/.kompany").expanduser()
    # Active workspace wins over the default.
    workspaces.register("brand-a", tmp_path / "a")
    workspaces.set_active("brand-a")
    assert workspaces.resolve_data_dir() == tmp_path / "a"


# ----- settings resolution order --------------------------------------------


def test_settings_use_active_workspace_data_dir(registry, tmp_path):
    ws_dir = tmp_path / "brand-a"
    ws_dir.mkdir()
    workspaces.register("brand-a", ws_dir)
    workspaces.set_active("brand-a")

    settings = KompanySettings.load(None)
    assert settings.data_dir == ws_dir


def test_settings_env_beats_workspace(registry, tmp_path, monkeypatch):
    workspaces.register("brand-a", tmp_path / "a")
    workspaces.set_active("brand-a")
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "pinned"))

    settings = KompanySettings.load(None)
    assert settings.data_dir == tmp_path / "pinned"


def test_settings_yaml_data_dir_in_workspace_still_honored(registry, tmp_path):
    """A data_dir key inside the active workspace's config.yaml wins
    over the workspace dir itself (issue #21 ordering unchanged)."""
    ws_dir = tmp_path / "brand-a"
    ws_dir.mkdir()
    redirected = tmp_path / "redirected"
    (ws_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {"company": {"name": "Brand A"}, "data_dir": str(redirected)}
        )
    )
    workspaces.register("brand-a", ws_dir)
    workspaces.set_active("brand-a")

    settings = KompanySettings.load(None)
    assert settings.data_dir == redirected
    assert settings.company_name == "Brand A"


# ----- REST: list / switch rebinds the engine / create ----------------------


def _seed_two_brands(tmp_path):
    for name, label in (("brand-a", "Brand A"), ("brand-b", "Brand B")):
        d = tmp_path / name
        d.mkdir()
        (d / "config.yaml").write_text(
            yaml.safe_dump({"company": {"name": label}})
        )
        workspaces.register(name, d, label=label)
    workspaces.set_active("brand-a")


def test_rest_switch_rebinds_engine(registry, tmp_path):
    from fastapi.testclient import TestClient

    from kompany.interfaces import api

    _seed_two_brands(tmp_path)
    api.reset_engine()
    client = TestClient(api.app)
    try:
        listing = client.get("/workspaces").json()
        assert listing["active"] == "brand-a"
        assert listing["env_override"] is False
        assert api.get_engine().settings.company_name == "Brand A"

        switched = client.post(
            "/workspaces/switch", json={"name": "brand-b"}
        ).json()
        assert switched["name"] == "brand-b"
        assert switched["restart_required"] is False
        # The cached engine was dropped — next access binds the new dir.
        assert api.get_engine().settings.company_name == "Brand B"
        assert api.get_engine().settings.data_dir == tmp_path / "brand-b"
    finally:
        api.reset_engine()


def test_rest_switch_unknown_returns_error(registry, tmp_path):
    from fastapi.testclient import TestClient

    from kompany.interfaces import api

    _seed_two_brands(tmp_path)
    api.reset_engine()
    client = TestClient(api.app)
    try:
        body = client.post("/workspaces/switch", json={"name": "nope"}).json()
        assert "error" in body
        # Failed switch must NOT drop the active binding.
        assert workspaces.workspaces_list()["active"] == "brand-a"
    finally:
        api.reset_engine()


def test_rest_switch_restart_required_when_env_pinned(
    registry, tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from kompany.interfaces import api

    _seed_two_brands(tmp_path)
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "pinned"))
    api.reset_engine()
    client = TestClient(api.app)
    try:
        switched = client.post(
            "/workspaces/switch", json={"name": "brand-b"}
        ).json()
        # Registry flipped, but THIS process is pinned by env — honest flag.
        assert switched["restart_required"] is True
    finally:
        api.reset_engine()


def test_rest_create_workspace(registry, tmp_path):
    from fastapi.testclient import TestClient

    from kompany.interfaces import api

    api.reset_engine()
    client = TestClient(api.app)
    try:
        body = client.post(
            "/workspaces", json={"name": "brand-c", "label": "Brand C"}
        ).json()
        assert body["name"] == "brand-c"
        assert Path(body["data_dir"]).is_dir()
        dup = client.post("/workspaces", json={"name": "brand-c"}).json()
        assert "error" in dup
    finally:
        api.reset_engine()


# ----- parity: CLI / MCP / SDK emit the REST shapes --------------------------


def test_parity_list_payload_identical_across_surfaces(registry, tmp_path):
    from kompany.interfaces.cli import app as cli_app
    from kompany.interfaces.mcp_dispatch.dispatcher import dispatch_tool

    _seed_two_brands(tmp_path)

    class _Engine:
        workspaces_list = staticmethod(workspaces.workspaces_list)

        def workspace_switch(self, name):
            entry = workspaces.set_active(name)
            entry["restart_required"] = False
            return entry

    canonical = workspaces.workspaces_list()
    assert dispatch_tool(_Engine(), "kompany_workspaces", {}) == canonical

    result = runner.invoke(cli_app, ["workspace", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == canonical

    mcp_switch = dispatch_tool(
        _Engine(), "kompany_workspace_switch", {"name": "brand-b"}
    )
    assert mcp_switch["name"] == "brand-b"
    assert mcp_switch["active"] is True

    bad = dispatch_tool(_Engine(), "kompany_workspace_switch", {"name": "x"})
    assert "error" in bad


def test_cli_switch_create_remove(registry, tmp_path):
    from kompany.interfaces.cli import app as cli_app

    _seed_two_brands(tmp_path)

    result = runner.invoke(cli_app, ["workspace", "switch", "brand-b", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["name"] == "brand-b"
    assert workspaces.workspaces_list()["active"] == "brand-b"

    result = runner.invoke(
        cli_app, ["workspace", "create", "brand-c", "--label", "C", "--json"]
    )
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert Path(body["data_dir"]).is_dir()

    result = runner.invoke(cli_app, ["workspace", "remove", "brand-a", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data_kept"] is True
    assert (tmp_path / "brand-a" / "config.yaml").exists()

    # Unknown switch exits non-zero.
    assert runner.invoke(cli_app, ["workspace", "switch", "nope"]).exit_code == 1


def test_mcp_workspace_tools_registered():
    from kompany.interfaces.mcp_server import TOOLS

    names = {t.name for t in TOOLS}
    assert {"kompany_workspaces", "kompany_workspace_switch"} <= names
    switch_tool = next(t for t in TOOLS if t.name == "kompany_workspace_switch")
    assert "name" in switch_tool.inputSchema["properties"]


def test_sdk_workspace_methods(registry, tmp_path):
    from kompany.interfaces.sdk import Kompany

    _seed_two_brands(tmp_path)

    class _Engine:
        workspaces_list = staticmethod(workspaces.workspaces_list)

        def workspace_switch(self, name):
            entry = workspaces.set_active(name)
            entry["restart_required"] = False
            return entry

        def workspace_create(self, name, label=""):
            return workspaces.create(name, label=label)

    sdk = Kompany.__new__(Kompany)
    sdk._engine = _Engine()
    assert sdk.workspaces_list() == workspaces.workspaces_list()
    assert sdk.workspace_switch("brand-b")["name"] == "brand-b"
    created = sdk.workspace_create("brand-c", label="C")
    assert Path(created["data_dir"]).is_dir()
