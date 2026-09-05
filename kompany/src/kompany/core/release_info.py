"""Release identity of the RUNNING engine + deployment drift (Stage C).

Two questions a production host must answer without anyone ssh-ing in:

1. *What is this code?* — ``release_identity()``. CI writes
   ``kompany/release.json`` into the wheel (version, commit, tag, run URL);
   a source checkout has no such file and reports ``source-checkout``; a
   hand-built wheel or PyInstaller bundle reports ``local-build``.

2. *Is it still what was deployed?* — ``sync_deployment_identity()``. The
   first time a GitHub-built release runs against a data dir it records
   itself in ``deploy_identity.json``. From then on, an engine that starts
   from anything *other* than a GitHub release on that data dir is
   **drift**: the 2026-07 incident (a production server quietly turned into
   an editable dev checkout) reproduced, and caught at boot instead of
   months later. ``check_deployment_drift`` turns drift into one open
   ``deployment_drift`` health event (deduped) that clears itself when a
   release runs again.

Machines that never ran a GitHub release (every dev box) never drift.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kompany.core import build_info as _bi

RELEASE_FILE = "release.json"
IDENTITY_FILE = "deploy_identity.json"
KIND_DEPLOYMENT_DRIFT = "deployment_drift"
SOURCE_GITHUB = "github-release"
SOURCE_CHECKOUT = "source-checkout"
SOURCE_LOCAL = "local-build"

_UNSET: Any = object()


def _read_packaged_release() -> dict[str, Any] | None:
    """``release.json`` shipped next to ``kompany/__init__.py`` by CI, if any."""
    try:
        from importlib.resources import files

        ref = files("kompany").joinpath(RELEASE_FILE)
        if not ref.is_file():
            return None
        data = json.loads(ref.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — identity is advisory, never fatal
        return None
    return data if isinstance(data, dict) else None


def release_identity(packaged: dict[str, Any] | None | Any = _UNSET,
                     git_root: Path | None | Any = _UNSET) -> dict[str, Any]:
    """Where the running code came from. Pure given its two inputs."""
    rel = _read_packaged_release() if packaged is _UNSET else packaged
    root = _bi._GIT_ROOT if git_root is _UNSET else git_root
    try:
        from kompany import __version__ as version
    except Exception:  # noqa: BLE001
        version = "unknown"
    if rel and rel.get("source") == "github-actions":
        return {
            "source": SOURCE_GITHUB,
            "version": str(rel.get("version") or version),
            "commit": str(rel.get("commit") or "unknown"),
            "release_tag": rel.get("tag"),
            "built_at": rel.get("built_at"),
            "repository": rel.get("repository"),
            "run_url": rel.get("run_url"),
        }
    commit = (_bi.read_head_sha(root) if root is not None else None) or "unknown"
    return {
        "source": SOURCE_CHECKOUT if root is not None else SOURCE_LOCAL,
        "version": version,
        "commit": commit,
        "release_tag": None,
        "built_at": None,
        "repository": None,
        "run_url": None,
    }


def _short(sha: str | None) -> str:
    return (sha or "unknown")[:12]


def sync_deployment_identity(data_dir: Path, identity: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record a GitHub release run; flag anything else after one. Never raises."""
    ident = identity or release_identity()
    path = Path(data_dir) / IDENTITY_FILE
    record: dict[str, Any] = {}
    try:
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            record = loaded if isinstance(loaded, dict) else {}
    except Exception:  # noqa: BLE001 — a corrupt file is treated as absent
        record = {}
    last = record.get("last_github_release")
    if ident["source"] == SOURCE_GITHUB:
        record["last_github_release"] = {
            "version": ident["version"],
            "commit": ident["commit"],
            "release_tag": ident.get("release_tag"),
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError:
            pass
        return {"drift": False, "expected": record["last_github_release"], "actual": ident}
    if not isinstance(last, dict):
        return {"drift": False, "expected": None, "actual": ident}
    return {
        "drift": True,
        "expected": last,
        "actual": ident,
        "hint": (
            f"This data dir last ran GitHub release {last.get('release_tag') or last.get('version')} "
            f"({_short(last.get('commit'))}); the engine now runs from a {ident['source']} "
            f"({_short(ident.get('commit'))}). Reinstall the release wheel from GitHub Releases — "
            f"or delete {path} if this machine has deliberately become a development box."
        ),
    }


def check_deployment_drift(health_events: Any, audit: Any, data_dir: Path) -> dict[str, Any]:
    """Boot-time hook: one open ``deployment_drift`` event while drifted, none otherwise."""
    result = sync_deployment_identity(data_dir)
    try:
        open_events = health_events.list(status="open", kind=KIND_DEPLOYMENT_DRIFT, limit=10)
    except Exception:  # noqa: BLE001
        return result
    if result["drift"] and not open_events:
        detail = {"expected": result["expected"], "actual": result["actual"], "hint": result["hint"]}
        event = health_events.record(kind=KIND_DEPLOYMENT_DRIFT, detail=detail)
        try:
            audit.record("health.deployment_drift", "record", detail={"event_id": event["id"], **detail})
        except Exception:  # noqa: BLE001 — audit mirror is best-effort
            pass
        result["event_id"] = event["id"]
    elif not result["drift"] and open_events:
        for ev in open_events:
            health_events.resolve(ev["id"], "continue", resolved_by="system")
        result["resolved_event_ids"] = [ev["id"] for ev in open_events]
    return result


__all__ = [
    "IDENTITY_FILE",
    "KIND_DEPLOYMENT_DRIFT",
    "RELEASE_FILE",
    "check_deployment_drift",
    "release_identity",
    "sync_deployment_identity",
]
