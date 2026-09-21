"""Persist and apply the founder's custom API connection without exposing keys."""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from kompany.core.model_source_ops import config_yaml_path
from kompany.llm.providers import Provider, list_openai_compatible_models

_save_lock = threading.Lock()


def validate_base_url(value: str) -> str:
    """Accept HTTP(S) endpoints, including local providers, without URL secrets."""
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and not any(c.isspace() or ord(c) < 32 for c in value)
            and "\\" not in value
        )
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Use an HTTP(S) base URL without credentials, query, or fragment.")
    return value


def connection_status(engine: Any) -> dict[str, Any]:
    """Presence only: not even a key suffix leaves the engine."""
    base = engine.settings.custom_base_url
    try:
        base = validate_base_url(base) if base else ""
    except ValueError:
        base = ""
    return {"base_url": base, "api_key_configured": bool(engine.settings.custom_api_key)}


def _persist(path: Path, base_url: str, api_key: str) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("Settings file must contain a YAML mapping.")
    custom = data.get("custom_llm") or {}
    if not isinstance(custom, dict):
        raise ValueError("Custom API settings must contain a YAML mapping.")
    data["custom_llm"] = {**custom, "api_key": api_key, "base_url": base_url}
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same-directory rename is atomic; mkstemp creates a private 0600 file.
    fd, name = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_connection(engine: Any, base_url: str, api_key: str) -> dict[str, Any]:
    """Persist before applying; discovery warnings never masquerade as success."""
    base_url = validate_base_url(base_url)
    api_key = api_key.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in api_key):
        raise ValueError("API key must not contain control characters.")
    with _save_lock:
        old_base = engine.settings.custom_base_url.strip().rstrip("/")
        if not api_key and engine.settings.custom_api_key and base_url != old_base:
            raise ValueError("Enter the API key again when changing the base URL.")
        key = api_key or engine.settings.custom_api_key
        try:
            _persist(config_yaml_path(engine), base_url, key)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise ValueError("Could not save the API connection. Check the settings file.") from exc
        engine.settings.custom_api_key = key
        engine.settings.custom_base_url = base_url
        # Existing calls retain their client; subsequent calls build one with
        # the new connection. Do not close clients still used by active work.
        engine.llm._openai_clients.pop(Provider.CUSTOM, None)
        try:
            engine.audit.record(
                "settings.custom_llm_changed", "Founder updated the custom API connection",
                detail={"api_key_configured": bool(key)},
            )
        except Exception:  # noqa: BLE001 — audit failure must not undo a saved connection
            pass
    warning = ""
    try:
        models = list_openai_compatible_models(base_url, key)
        if not models:
            warning = "Saved, but the endpoint returned no models. Inference is not verified."
        elif engine.settings.model_primary not in models:
            warning = "Saved. The active model is not listed; choose a model under LLM Model."
    except Exception:  # noqa: BLE001 — provider errors can contain credentials
        warning = "Saved, but model discovery failed. Check the endpoint and key. Inference is not verified."
    return {**connection_status(engine), "warning": warning}
