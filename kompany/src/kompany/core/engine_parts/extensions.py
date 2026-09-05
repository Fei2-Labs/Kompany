"""Engine ops for customer extensions (07-24 four-layer, layer 3).

install → ``extension_activate`` approval card (executable code needs an
explicit yes) → active → ``extension_run`` in the isolated worker. A Core
release whose version leaves the manifest's ``core_api`` range *blocks* the
extension (status ``blocked`` + ``extension_incompatible`` health event);
nothing is deleted, and the block lifts by itself when a compatible Core
runs again. Same dict on CLI / REST / MCP / SDK.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from kompany.core.extensions.manifest import (
    ExtensionManifest,
    ManifestError,
    core_compatible,
    load_manifest,
    package_hash,
)
from kompany.state.models import ApprovalRequest

ACTION_EXTENSION_ACTIVATE = "extension_activate"
KIND_EXTENSION_INCOMPATIBLE = "extension_incompatible"


def _core_version() -> str:
    try:
        from kompany import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return "0.0.0+unknown"


class ExtensionsMixin:
    """Requires ``self.extensions`` (ExtensionStore), ``settings``, ``approvals``,
    ``audit``, ``health_events``, ``register_approval_effect``."""

    # -- layout -------------------------------------------------------------

    def _extensions_root(self) -> Path:
        return Path(self.settings.data_dir) / "extensions"

    def _extension_dirs(self, ext_id: str, version: str) -> tuple[Path, Path]:
        base = self._extensions_root() / ext_id
        return base / "pkg" / version, base / "data"

    # -- lifecycle ----------------------------------------------------------

    def extension_install(self, source: str | Path) -> dict[str, Any]:
        """Copy a package dir into the customer layer, validate, file the card."""
        src = Path(source).expanduser().resolve()
        if not src.is_dir():
            raise ValueError(f"extension source is not a directory: {src}")
        manifest = load_manifest(src)
        digest = package_hash(src)
        if manifest.sha256 and manifest.sha256.lower() != digest:
            raise ManifestError(f"package hash {digest[:12]} does not match manifest sha256 {manifest.sha256[:12]}")
        pkg_dir, data_dir = self._extension_dirs(manifest.id, manifest.version)
        previous = self.extensions.get(manifest.id)
        if pkg_dir.exists():
            shutil.rmtree(pkg_dir)
        shutil.copytree(src, pkg_dir, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        data_dir.mkdir(parents=True, exist_ok=True)
        ok, reason = core_compatible(manifest.core_api, _core_version())
        row = self.extensions.upsert(
            manifest.model_dump(), artifact_hash=digest, pkg_path=str(pkg_dir), status="installed",
            previous_version=previous["version"] if previous and previous["version"] != manifest.version else None,
        )
        if not ok:
            row = self.extensions.set_status(manifest.id, "blocked", reason=f"core_api: {reason}") or row
            self._extension_incompatible_event(manifest.id, reason)
        request = ApprovalRequest(
            action_type=ACTION_EXTENSION_ACTIVATE,
            summary=f"Activate extension {manifest.id} v{manifest.version} ({manifest.owner})"
                    + ("" if ok else " — BLOCKED: incompatible Core"),
            payload={"extension_id": manifest.id, "version": manifest.version, "owner": manifest.owner,
                     "origin": manifest.origin, "artifact_hash": digest, "core_api": manifest.core_api,
                     "core_compatible": ok, "capabilities": manifest.capabilities.model_dump(),
                     "description": manifest.description},
            requested_by="extension_layer", severity="high",
        )
        self.approvals.create(request)
        row = self.extensions.set_status(manifest.id, row["status"], approval_id=request.id) or row
        self.audit.record(
            "extension.installed", f"Extension {manifest.id} v{manifest.version} installed ({row['status']})",
            detail={"extension_id": manifest.id, "version": manifest.version, "artifact_hash": digest,
                    "core_compatible": ok, "approval_id": request.id, "capabilities": manifest.capabilities.model_dump()},
        )
        return row

    def extensions_list(self) -> list[dict[str, Any]]:
        return self.extensions.list()

    def extension_show(self, extension_id: str) -> dict[str, Any] | None:
        row = self.extensions.get(extension_id)
        if row is None:
            return None
        return {**row, "runs": self.extensions.runs(extension_id, limit=10)}

    def extension_set_enabled(self, extension_id: str, enabled: bool) -> dict[str, Any] | None:
        row = self.extensions.get(extension_id)
        if row is None:
            return None
        if row["status"] == "blocked":
            return row  # a block is Core's verdict; enable/disable does not override it
        if enabled and row["status"] == "disabled":
            row = self.extensions.set_status(extension_id, "active") or row
        elif not enabled and row["status"] == "active":
            row = self.extensions.set_status(extension_id, "disabled") or row
        self.audit.record("extension.toggled", f"Extension {extension_id} → {row['status']}",
                          detail={"extension_id": extension_id, "status": row["status"]})
        return row

    def extension_remove(self, extension_id: str) -> dict[str, Any] | None:
        """Status only — package + data stay on disk for rollback/autopsy."""
        row = self.extensions.set_status(extension_id, "removed")
        if row:
            self.audit.record("extension.removed", f"Extension {extension_id} removed (files kept)",
                              detail={"extension_id": extension_id, "pkg_path": row["pkg_path"]})
        return row

    # -- execution ----------------------------------------------------------

    def extension_run(self, extension_id: str, job: dict[str, Any] | None = None,
                      *, timeout_seconds: int = 120) -> dict[str, Any]:
        from kompany.core.extensions.worker import run_extension

        row = self.extensions.get(extension_id)
        if row is None:
            raise ValueError(f"unknown extension: {extension_id}")
        ok, reason = core_compatible(row["manifest"].get("core_api", ""), _core_version())
        if not ok and row["status"] != "blocked":
            self.extensions.set_status(extension_id, "blocked", reason=f"core_api: {reason}")
            self._extension_incompatible_event(extension_id, reason)
            row = self.extensions.get(extension_id) or row
        if row["status"] != "active":
            return {"ok": False, "extension_id": extension_id, "status": row["status"],
                    "error": f"extension is {row['status']}" + (f": {row['block_reason']}" if row.get("block_reason") else
                                                                 " — approve its activation card first" if row["status"] == "installed" else "")}
        manifest = ExtensionManifest.model_validate(row["manifest"])
        pkg_dir, data_dir = self._extension_dirs(manifest.id, manifest.version)
        run_id = uuid4().hex[:12]
        outcome = run_extension(self, manifest, pkg_dir, data_dir, dict(job or {}), run_id=run_id,
                                timeout_seconds=timeout_seconds)
        recorded = self.extensions.record_run(extension_id, {"run_id": run_id, **outcome.as_dict()})
        self.audit.record(
            "extension.run", f"Extension {extension_id} run {run_id}: {'ok' if outcome.ok else 'failed'}",
            detail={"extension_id": extension_id, "run_id": run_id, "ok": outcome.ok, "exit_code": outcome.exit_code,
                    "requests": outcome.requests, "denied": outcome.denied, "proposals": outcome.proposals,
                    "error": outcome.error},
        )
        return recorded

    # -- compatibility (plan step 4) ----------------------------------------

    def extensions_compat_check(self) -> dict[str, Any]:
        """Boot-time: block newly incompatible extensions, unblock compatible ones."""
        version = _core_version()
        blocked: list[str] = []; unblocked: list[str] = []
        for row in self.extensions.list():
            ok, reason = core_compatible(row["manifest"].get("core_api", ""), version)
            if not ok and row["status"] not in ("blocked", "removed"):
                self.extensions.set_status(row["id"], "blocked", reason=f"core_api: {reason}")
                self._extension_incompatible_event(row["id"], reason)
                blocked.append(row["id"])
            elif ok and row["status"] == "blocked" and str(row.get("block_reason") or "").startswith("core_api"):
                self.extensions.unblock(row["id"])
                self._resolve_incompatible_events(row["id"])
                unblocked.append(row["id"])
        return {"core_version": version, "blocked": blocked, "unblocked": unblocked}

    def _extension_incompatible_event(self, extension_id: str, reason: str) -> None:
        try:
            open_ = [e for e in self.health_events.list(status="open", kind=KIND_EXTENSION_INCOMPATIBLE, limit=200)
                     if (e.get("detail") or {}).get("extension_id") == extension_id]
            if open_:
                return
            ev = self.health_events.record(kind=KIND_EXTENSION_INCOMPATIBLE, detail={
                "extension_id": extension_id, "reason": reason, "core_version": _core_version(),
                "hint": "The extension is blocked, not deleted. Update it (or roll Core back) and it unblocks itself."})
            self.audit.record("extension.incompatible", f"Extension {extension_id} blocked: {reason}",
                              detail={"extension_id": extension_id, "reason": reason, "event_id": ev["id"]})
        except Exception:  # noqa: BLE001 — advisory
            pass

    def _resolve_incompatible_events(self, extension_id: str) -> None:
        try:
            for e in self.health_events.list(status="open", kind=KIND_EXTENSION_INCOMPATIBLE, limit=200):
                if (e.get("detail") or {}).get("extension_id") == extension_id:
                    self.health_events.resolve(e["id"], "continue", resolved_by="system")
        except Exception:  # noqa: BLE001
            pass

    # -- approval effect -----------------------------------------------------

    def _bind_extension_effects(self) -> None:
        self.register_approval_effect(ACTION_EXTENSION_ACTIVATE, _approve_activation, _reject_activation)


def _approve_activation(engine: Any, request: ApprovalRequest) -> dict[str, Any]:
    payload = request.payload or {}
    if payload.get("effect_applied"):
        return {"status": "already_applied"}
    ext_id = payload.get("extension_id")
    row = engine.extensions.get(ext_id) if ext_id else None
    if row is None:
        return {"status": "invalid_payload"}
    if row["status"] == "blocked":
        engine.approvals.update_payload(request.id, {"effect_applied": True, "activated": False})
        engine.audit.record("extension.activation_deferred", f"Extension {ext_id} approved but blocked",
                            detail={"extension_id": ext_id, "approval_id": request.id, "block_reason": row["block_reason"]})
        return {"status": "blocked", "detail": row["block_reason"]}
    engine.extensions.set_status(ext_id, "active", approval_id=request.id)
    engine.approvals.update_payload(request.id, {"effect_applied": True, "activated": True})
    engine.audit.record("extension.activated", f"Extension {ext_id} activated",
                        detail={"extension_id": ext_id, "approval_id": request.id, "version": row["version"]})
    return {"status": "activated", "extension_id": ext_id}


def _reject_activation(engine: Any, request: ApprovalRequest) -> dict[str, Any]:
    payload = request.payload or {}
    ext_id = payload.get("extension_id")
    if ext_id and engine.extensions.get(ext_id):
        engine.extensions.set_status(ext_id, "disabled", approval_id=request.id)
        engine.audit.record("extension.activation_rejected", f"Extension {ext_id} activation rejected",
                            detail={"extension_id": ext_id, "approval_id": request.id})
    return {"status": "rejected", "extension_id": ext_id}


__all__ = ["ACTION_EXTENSION_ACTIVATE", "KIND_EXTENSION_INCOMPATIBLE", "ExtensionsMixin"]
