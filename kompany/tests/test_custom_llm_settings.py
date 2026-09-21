"""Custom API Settings persistence, live refresh, and secret hygiene."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from fastapi.testclient import TestClient

from kompany.config.settings import KompanySettings
from kompany.core import custom_llm_settings as ops
from kompany.interfaces.api import app
from kompany.llm.providers import Provider


@pytest.fixture
def engine(tmp_path, monkeypatch):
    settings = KompanySettings(
        _env_file=None, data_dir=tmp_path,
        CUSTOM_LLM_API_KEY="test-old-key", CUSTOM_LLM_BASE_URL="https://old.example/v1",
    )
    settings.model_primary = "test-model"
    eng = SimpleNamespace(
        settings=settings, _config_path=None, audit=Mock(),
        llm=SimpleNamespace(_openai_clients={Provider.CUSTOM: object(), Provider.OPENAI: object()}),
    )
    monkeypatch.setattr("kompany.interfaces.api._engine", eng)
    monkeypatch.setattr(ops, "list_openai_compatible_models", lambda *args: ["test-model"])
    return eng


def test_yaml_aliases_override_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "test-env-key")
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "https://env.example/v1")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"custom_llm": {
        "api_key": "test-yaml-key", "base_url": "https://yaml.example/v1",
    }}))
    loaded = KompanySettings.load(str(path))
    assert loaded.custom_api_key == "test-yaml-key"
    assert loaded.custom_base_url == "https://yaml.example/v1"
    path.write_text("company: {}\n")
    loaded = KompanySettings.load(str(path))
    assert loaded.custom_api_key == "test-env-key"
    assert loaded.custom_base_url == "https://env.example/v1"


def test_save_preserves_other_settings_and_refreshes_client(engine, tmp_path):
    path = tmp_path / "explicit.yaml"
    engine._config_path = str(path)
    path.write_text("company:\n  name: Test\ncustom_llm:\n  extra: keep\n")
    other_client = engine.llm._openai_clients[Provider.OPENAI]
    result = ops.save_connection(engine, "https://new.example/v1/", "test-new-key")
    assert result == {"base_url": "https://new.example/v1", "api_key_configured": True, "warning": ""}
    data = yaml.safe_load(path.read_text())
    assert data["company"] == {"name": "Test"}
    assert data["custom_llm"]["extra"] == "keep"
    assert path.stat().st_mode & 0o777 == 0o600
    assert KompanySettings.load(str(path)).custom_api_key == "test-new-key"
    assert engine.settings.custom_api_key == "test-new-key"
    assert Provider.CUSTOM not in engine.llm._openai_clients
    assert engine.llm._openai_clients[Provider.OPENAI] is other_client
    assert "test-new-key" not in repr(engine.audit.mock_calls)


def test_blank_key_preserves_existing(engine):
    ops.save_connection(engine, engine.settings.custom_base_url, "")
    assert engine.settings.custom_api_key == "test-old-key"


def test_new_endpoint_requires_explicit_key(engine, tmp_path):
    with pytest.raises(ValueError, match="again"):
        ops.save_connection(engine, "https://new.example/v1", "")
    assert not (tmp_path / "config.yaml").exists()
    assert engine.settings.custom_base_url == "https://old.example/v1"


@pytest.mark.parametrize("url", ["", "file:///tmp/a", "https://u:secret@example.com", "https://x/?key=secret", "https://x/#secret", "https://x:bad", "https://x\n/v1", "https://x\\@evil.example"])
def test_invalid_url_never_written(engine, tmp_path, url):
    with pytest.raises(ValueError):
        ops.save_connection(engine, url, "test-new-key")
    assert not (tmp_path / "config.yaml").exists()


def test_keyless_local_provider(engine):
    engine.settings.custom_api_key = ""
    result = ops.save_connection(engine, "http://127.0.0.1:1234/v1", "")
    assert result["api_key_configured"] is False


def test_persistence_failure_leaves_live_state_unchanged(engine, monkeypatch):
    def fail(*args):
        raise OSError("sensitive diagnostic")
    monkeypatch.setattr(ops.os, "replace", fail)
    with pytest.raises(ValueError, match="Could not save") as error:
        ops.save_connection(engine, "https://new.example/v1", "test-new-key")
    assert "sensitive" not in str(error.value)
    assert engine.settings.custom_api_key == "test-old-key"
    assert Provider.CUSTOM in engine.llm._openai_clients


def test_discovery_failure_saved_with_safe_warning(engine, monkeypatch):
    def fail(*args):
        raise RuntimeError("test-new-key provider echoed credential")
    monkeypatch.setattr(ops, "list_openai_compatible_models", fail)
    result = ops.save_connection(engine, "https://new.example/v1", "test-new-key")
    assert "discovery failed" in result["warning"]
    assert "test-new-key" not in str(result)
    assert engine.settings.custom_api_key == "test-new-key"


@pytest.mark.parametrize("models, expected", [([], "no models"), (["different"], "active model")])
def test_discovery_warnings(engine, monkeypatch, models, expected):
    monkeypatch.setattr(ops, "list_openai_compatible_models", lambda *args: models)
    assert expected in ops.save_connection(engine, engine.settings.custom_base_url, "")["warning"]


def test_rest_requires_dashboard_token_when_configured(engine):
    engine.settings.web_dashboard_token = "test-dashboard-token"
    client = TestClient(app)
    assert client.get("/settings/custom-llm").status_code == 401
    assert client.put("/settings/custom-llm", json={}).status_code == 401
    assert client.get("/settings/custom-llm", headers={
        "Authorization": "Bearer test-dashboard-token",
    }).status_code == 200


def test_rest_secret_hygiene(engine, monkeypatch):
    client = TestClient(app)
    assert client.get("/settings/custom-llm").json() == {
        "base_url": "https://old.example/v1", "api_key_configured": True,
    }
    for payload in [
        {"base_url": "https://new.example/v1", "api_key": "test-new-key"},
        {"base_url": ["test-new-key"], "api_key": "test-new-key"},
        {"base_url": "https://new.example/v1", "api_key": ["test-new-key"]},
        {"base_url": "https://new.example/v1", "secret": "test-new-key"},
    ]:
        response = client.put("/settings/custom-llm", json=payload)
        assert response.status_code in {200, 422}
        assert "test-new-key" not in response.text
    def fail(*args):
        raise RuntimeError("test-new-key")
    monkeypatch.setattr("kompany.llm.providers.list_openai_compatible_models", fail)
    response = client.get("/settings/model")
    assert response.status_code == 200
    assert "test-new-key" not in response.text
    assert "discovery failed" in response.json()["error"]
