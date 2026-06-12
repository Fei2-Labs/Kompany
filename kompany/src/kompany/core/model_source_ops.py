"""ModelSource engine operations — get / set / clear + agent-CLI detection.

Founder surface for PRD ``06-11-harness-execution-leg`` D1/D2 (PR5b).
The founder configures a *model source* (custom API key, Claude
subscription, OpenAI subscription); the engine derives the execution
loop internally. Nothing here ever asks for — or exposes as an input —
a "runner"/vehicle choice; the derived vehicle is included in the
serialized payload as read-only information only.

Persistence: the active source is written to the engine's settings YAML
(the explicit ``config_path`` the engine was constructed with, falling
back to ``<data_dir>/config.yaml``). ``KompanySettings.load`` reads the
same file back on the next boot, so a Settings change survives engine
restarts on every interface.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import ValidationError

from kompany.config.model_source import ModelSource

# Agent CLI binaries we probe for, mapped to the ModelSource kind each
# one unlocks (claude → Claude subscription, codex → OpenAI
# subscription, opencode → any API key). Same ``which()`` detection
# pattern as the #18 claude-code provider precedent.
AGENT_CLI_KINDS: dict[str, str] = {
    "claude": "claude_subscription",
    "codex": "openai_subscription",
    "opencode": "custom_api",
}

# Best-effort ``<cli> --version`` probe timeout (seconds).
VERSION_PROBE_TIMEOUT = 5.0

# Founder-facing execution summary per source kind. NEVER says
# "runner"/"vehicle" — it describes which CLI carries the work.
EXECUTION_SUMMARY: dict[str, str] = {
    "claude_subscription": (
        "Tasks execute via the Claude Code CLI on your Claude "
        "subscription — per-call spend books as shadow value; the "
        "monthly fee is the real recurring expense."
    ),
    "openai_subscription": (
        "Tasks execute via the Codex CLI on your OpenAI subscription — "
        "per-call spend books as shadow value; the monthly fee is the "
        "real recurring expense."
    ),
    "custom_api": (
        "Tasks execute via the opencode CLI with your API key — every "
        "call books real per-token cost to the ledger."
    ),
}

AUDIT_EVENT = "settings.model_source_changed"


def serialize_source(source: ModelSource | None) -> dict[str, Any] | None:
    """Wire shape shared by all four interfaces (interfaces.md rule).

    ``ModelSource.model_dump()`` plus the derived ``vehicle`` (read-only
    info — never an input) and a founder-facing ``execution_summary``.
    """
    if source is None:
        return None
    payload = source.model_dump()
    payload["vehicle"] = source.vehicle
    payload["execution_summary"] = EXECUTION_SUMMARY.get(source.kind, "")
    return payload


def config_yaml_path(engine: Any) -> Path:
    """Settings YAML the active model source persists to.

    The explicit ``config_path`` the engine was constructed with wins;
    otherwise ``<data_dir>/config.yaml`` (the path
    :meth:`KompanySettings.load` falls back to on a bare boot).
    """
    explicit = getattr(engine, "_config_path", None)
    if explicit:
        return Path(explicit)
    return Path(engine.settings.data_dir) / "config.yaml"


def get_model_source(engine: Any) -> dict[str, Any] | None:
    """Serialize the active model source; ``None`` = legacy api billing."""
    return serialize_source(getattr(engine.settings, "model_source", None))


def set_model_source(engine: Any, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Validate, persist, and apply a new model source.

    ``payload=None`` clears the source (back to the legacy per-token
    path). Validation errors surface as ``ValueError`` so every
    interface renders the same message. The audit record carries kind +
    billing_mode only — never auth material or pricing details.
    """
    if payload is None:
        source: ModelSource | None = None
    else:
        try:
            source = ModelSource.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(_validation_message(exc)) from exc

    path = config_yaml_path(engine)
    data: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    if source is None:
        data.pop("model_source", None)
    else:
        data["model_source"] = source.model_dump(exclude_none=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    engine.settings.model_source = source

    detail: dict[str, Any]
    if source is None:
        detail = {"cleared": True}
        message = "Model source cleared (legacy per-token billing)"
    else:
        detail = {"kind": source.kind, "billing_mode": source.billing_mode}
        message = f"Model source set to {source.kind}"
    try:
        engine.audit.record(AUDIT_EVENT, message, detail=detail)
    except Exception:  # noqa: BLE001 — audit miss must not block settings
        pass
    return {"source": serialize_source(source)}


def detect_agent_clis(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, dict[str, Any]]:
    """Probe PATH for the agent CLIs that unlock zero-key model sources.

    Returns ``{cli_name: {found, path, version, source_kind}}``.
    Version is best-effort (``<cli> --version`` with a short timeout) —
    a hung or weird binary never breaks detection.
    """
    out: dict[str, dict[str, Any]] = {}
    for name, kind in AGENT_CLI_KINDS.items():
        path = which(name)
        info: dict[str, Any] = {
            "found": path is not None,
            "path": path,
            "version": None,
            "source_kind": kind,
        }
        if path is not None:
            info["version"] = _probe_version(name, run)
        out[name] = info
    return out


def _probe_version(name: str, run: Callable[..., Any]) -> str | None:
    try:
        proc = run(
            [name, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_PROBE_TIMEOUT,
        )
    except Exception:  # noqa: BLE001 — version is decorative
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    text = (getattr(proc, "stdout", "") or getattr(proc, "stderr", "") or "").strip()
    first_line = text.splitlines()[0].strip() if text else ""
    return first_line[:80] or None


def _validation_message(exc: ValidationError) -> str:
    """First error message from a pydantic ValidationError, founder-readable."""
    errors = exc.errors()
    if errors:
        return str(errors[0].get("msg", "invalid model source"))
    return "invalid model source"


__all__ = [
    "AGENT_CLI_KINDS",
    "AUDIT_EVENT",
    "EXECUTION_SUMMARY",
    "config_yaml_path",
    "detect_agent_clis",
    "get_model_source",
    "serialize_source",
    "set_model_source",
]
