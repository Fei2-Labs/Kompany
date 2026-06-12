"""Workspace registry — one isolated data dir per brand (issue #15).

Decision A: a workspace IS a data dir. Each brand keeps its own DB,
credential vault, ledger, and integration credentials — hard isolation,
nothing is ever shared across workspaces. This module only manages the
*registry* (which dirs exist + which one is active); it never touches
the data inside a workspace.

Registry file: ``~/.kompany-workspaces.json`` — deliberately OUTSIDE any
workspace data dir so switching never moves the registry itself. Shape::

    {
      "active": "default",
      "workspaces": {
        "default": {"data_dir": "~/.kompany", "label": "Default",
                     "created_at": "..."}
      }
    }

Precedence contract (must match ``KompanySettings.load``):

1. ``KOMPANY_DATA_DIR`` env — explicit override, registry is BYPASSED
   entirely (the daemon plist pins a brand this way; one plist per
   brand for multi-brand daemons, future work).
2. The active workspace's ``data_dir`` from this registry.
3. ``~/.kompany`` default.

A ``data_dir`` key inside the chosen workspace's ``config.yaml`` still
applies after step 2/3 (issue #21 behaviour is unchanged).

Tests override the registry location via ``KOMPANY_WORKSPACES_FILE``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_REGISTRY_FILE = "~/.kompany-workspaces.json"
DEFAULT_WORKSPACES_ROOT = "~/.kompany-workspaces"
DEFAULT_WORKSPACE_NAME = "default"
DEFAULT_DATA_DIR = "~/.kompany"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class WorkspaceError(ValueError):
    """Invalid workspace operation (unknown name, duplicate, bad name)."""


def registry_path() -> Path:
    """Registry file location (``KOMPANY_WORKSPACES_FILE`` overrides)."""
    override = os.environ.get("KOMPANY_WORKSPACES_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(DEFAULT_REGISTRY_FILE).expanduser()


def _seed_registry() -> dict[str, Any]:
    """Fresh registry: the legacy single data dir becomes ``default``."""
    return {
        "active": DEFAULT_WORKSPACE_NAME,
        "workspaces": {
            DEFAULT_WORKSPACE_NAME: {
                "data_dir": DEFAULT_DATA_DIR,
                "label": "Default",
                "created_at": datetime.now(UTC).isoformat(),
            },
        },
    }


def load_registry() -> dict[str, Any]:
    """Read the registry, seeding the default migration when missing.

    Read-only: the seed is returned in memory; the file is only written
    by mutating ops (register/set_active/remove). A corrupt registry
    file also falls back to the seed rather than crashing engine boot.
    """
    path = registry_path()
    if not path.exists():
        return _seed_registry()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return _seed_registry()
    if not isinstance(data, dict) or not isinstance(data.get("workspaces"), dict):
        return _seed_registry()
    if not data["workspaces"]:
        return _seed_registry()
    if data.get("active") not in data["workspaces"]:
        data["active"] = next(iter(data["workspaces"]))
    return data


def _save_registry(data: dict[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def _entry_payload(name: str, entry: dict[str, Any], active: str) -> dict[str, Any]:
    return {
        "name": name,
        "label": entry.get("label") or name,
        "data_dir": str(Path(str(entry.get("data_dir", ""))).expanduser()),
        "created_at": entry.get("created_at"),
        "active": name == active,
    }


def workspaces_list() -> dict[str, Any]:
    """Canonical list payload — identical across CLI/REST/MCP/SDK.

    ``env_override`` flags that ``KOMPANY_DATA_DIR`` is set, i.e. the
    registry's active entry is currently being bypassed.
    """
    data = load_registry()
    active = data["active"]
    return {
        "active": active,
        "env_override": bool(os.environ.get("KOMPANY_DATA_DIR", "").strip()),
        "workspaces": [
            _entry_payload(name, entry, active)
            for name, entry in sorted(data["workspaces"].items())
        ],
    }


def register(name: str, data_dir: str | Path, label: str = "") -> dict[str, Any]:
    """Add a workspace entry pointing at ``data_dir``. Does not mkdir."""
    if not _NAME_RE.match(name):
        raise WorkspaceError(
            f"Invalid workspace name {name!r}: use lowercase letters, "
            "digits, '-' or '_' (max 64 chars)."
        )
    data = load_registry()
    if name in data["workspaces"]:
        raise WorkspaceError(f"Workspace {name!r} already exists.")
    entry = {
        "data_dir": str(data_dir),
        "label": label or name,
        "created_at": datetime.now(UTC).isoformat(),
    }
    data["workspaces"][name] = entry
    _save_registry(data)
    return _entry_payload(name, entry, data["active"])


def set_active(name: str) -> dict[str, Any]:
    """Mark ``name`` as the active workspace and persist the registry."""
    data = load_registry()
    if name not in data["workspaces"]:
        known = ", ".join(sorted(data["workspaces"]))
        raise WorkspaceError(f"Unknown workspace {name!r}. Known: {known}.")
    data["active"] = name
    _save_registry(data)
    return _entry_payload(name, data["workspaces"][name], name)


def remove(name: str) -> dict[str, Any]:
    """Remove the registry ENTRY only — the data dir is never deleted.

    The active workspace cannot be removed (switch away first); the
    brand's vault/ledger/DB stay on disk for re-registration.
    """
    data = load_registry()
    if name not in data["workspaces"]:
        known = ", ".join(sorted(data["workspaces"]))
        raise WorkspaceError(f"Unknown workspace {name!r}. Known: {known}.")
    if name == data["active"]:
        raise WorkspaceError(
            f"Workspace {name!r} is active — switch to another workspace "
            "before removing it."
        )
    entry = data["workspaces"].pop(name)
    _save_registry(data)
    payload = _entry_payload(name, entry, data["active"])
    payload["removed"] = True
    payload["data_kept"] = True
    return payload


def create(name: str, label: str = "") -> dict[str, Any]:
    """Create a fresh workspace dir under ``~/.kompany-workspaces/<name>``
    and register it. Onboarding then runs against it after a switch."""
    root = Path(
        os.environ.get("KOMPANY_WORKSPACES_ROOT", DEFAULT_WORKSPACES_ROOT)
    ).expanduser()
    target = root / name
    payload = register(name, str(target), label=label)  # validates name
    target.mkdir(parents=True, exist_ok=True)
    return payload


def active_data_dir() -> Path | None:
    """Active workspace's data dir, or ``None`` when env bypasses it.

    Returns ``None`` when ``KOMPANY_DATA_DIR`` is set (explicit override
    wins — the registry is not consulted at all in that case).
    """
    if os.environ.get("KOMPANY_DATA_DIR", "").strip():
        return None
    data = load_registry()
    entry = data["workspaces"][data["active"]]
    return Path(str(entry["data_dir"])).expanduser()


def resolve_data_dir() -> Path:
    """Full chain: env ``KOMPANY_DATA_DIR`` > active workspace > default.

    Single helper for surfaces that need the engine's data dir without
    booting an engine (sidecar discovery, onboarding status).
    """
    env = os.environ.get("KOMPANY_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    ws = active_data_dir()
    if ws is not None:
        return ws
    return Path(DEFAULT_DATA_DIR).expanduser()
