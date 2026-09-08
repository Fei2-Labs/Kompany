"""Durable update state: ``<data_dir>/update/state.json`` (survives the restart)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

PHASES = ("idle", "checking", "backing_up", "downloading", "verifying", "installing", "switching", "restarting",
          "done", "failed", "rolled_back")


class UpdateState(BaseModel):
    phase: str = "idle"
    mode: str = "manual"
    installed_version: str | None = None
    installed_pro_version: str | None = None
    target_version: str | None = None
    previous_version: str | None = None
    latest: dict[str, Any] = Field(default_factory=dict)  # package → ReleaseInfo.as_dict()
    update_available: bool = False
    last_check_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    attestation: dict[str, Any] = Field(default_factory=dict)
    backup_id: str | None = None
    error: str | None = None
    restart_required: bool = False
    rollback_attempted: bool = False
    verify_pending: bool = False

    def step(self, name: str, detail: str = "") -> None:
        self.steps.append({"at": datetime.now(UTC).isoformat(), "step": name, "detail": detail[:500]})
        self.steps = self.steps[-40:]


def state_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "update" / "state.json"


def load_state(data_dir: Path | str) -> UpdateState:
    p = state_path(data_dir)
    if not p.exists():
        return UpdateState()
    try:
        return UpdateState.model_validate_json(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — corrupt state must not block the engine
        return UpdateState()


def save_state(data_dir: Path | str, state: UpdateState) -> None:
    p = state_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(p)


__all__ = ["PHASES", "UpdateState", "load_state", "save_state", "state_path"]
