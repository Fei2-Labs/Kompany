"""Doctor checks added for the self-test gate (08-29 R1).

Loadability of every artifact source (builtin souls, Pro + workspace souls
and workflows, plugin discovery errors), ledger integrity, and the state of
the workspace artifact repo. All offline, all cheap; a broken check is a
``warn`` node, never an exception (``doctor._guard``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from kompany.core.doctor import node

_SOULS_DIR = Path(__file__).resolve().parents[1] / "agents" / "souls"


def check_souls(engine: Any) -> dict[str, Any]:
    """Every builtin soul YAML parses; every Pro/workspace soul loads through
    the reserved-role guard."""
    from kompany.agents.soul_agent import _load_yaml
    from kompany.plugins.loader import discover

    problems: list[str] = []
    builtin = 0
    for p in sorted(_SOULS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not data.get("role") or not data.get("display_name"):
                problems.append(f"{p.name}: missing role/display_name")
            builtin += 1
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{p.name}: {type(exc).__name__}")
    found = discover(getattr(engine.settings, "data_dir", None))
    extra = 0
    for soul in found.get("soul", []):
        src = getattr(soul, "soul_yaml", None)
        if not src:
            extra += 1; continue
        try:
            _load_yaml(src); extra += 1
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{Path(str(src)).name}: {exc}")
    for group, name, err in found.get("_errors", []):
        if group.startswith("workspace.souls") or group == "kompany.souls":
            problems.append(f"{name}: {err[:120]}")
    detail = f"{builtin} builtin, {extra} plugin/workspace souls load"
    if problems:
        return node("souls", "Souls", "fail", detail + f"; {len(problems)} broken",
                    "Fix or remove the listed soul YAML: " + "; ".join(problems[:5]))
    return node("souls", "Souls", "ok", detail)


def check_workflows(engine: Any) -> dict[str, Any]:
    from kompany.core import workflows_registry as reg

    problems: list[str] = []
    ids = reg.list_workflows()
    for wid in ids:
        try:
            reg.get(wid)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{wid}: {type(exc).__name__}: {str(exc)[:80]}")
    try:
        from kompany.plugins.loader import discover

        for group, name, err in discover(getattr(engine.settings, "data_dir", None)).get("_errors", []):
            if group.startswith("workspace.workflows") or group == "kompany.workflows":
                problems.append(f"{name}: {err[:120]}")
    except Exception:  # noqa: BLE001
        pass
    detail = f"{len(ids)} workflows resolve"
    if problems:
        return node("workflows", "Workflows", "fail", detail + f"; {len(problems)} broken",
                    "Fix the listed workflow YAML: " + "; ".join(problems[:5]))
    return node("workflows", "Workflows", "ok", detail)


def check_plugins(engine: Any) -> dict[str, Any]:
    """Entry-point discovery + contract version; errors from broken wheels."""
    from kompany.plugins import __contract_version__
    from kompany.plugins.loader import discover

    found = discover(getattr(engine.settings, "data_dir", None))
    counts = {k: len(v) for k, v in found.items() if not k.startswith("_")}
    errors = [e for e in found.get("_errors", []) if not e[0].startswith("workspace")]
    detail = f"contract {__contract_version__}; " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()) if v)
    if errors:
        return node("plugins", "Plugins", "warn", detail + f"; {len(errors)} failed to load",
                    "Reinstall or remove the failing plugin: " + "; ".join(f"{g}:{n}" for g, n, _ in errors[:5]))
    return node("plugins", "Plugins", "ok", detail)


def check_ledger(engine: Any) -> dict[str, Any]:
    """Running balance must equal the sum of amounts; no NULL amounts."""
    db = engine.db
    row = db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total, "
                     "SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) AS nulls FROM ledger").fetchone()
    n, total, nulls = int(row["n"]), float(row["total"] or 0.0), int(row["nulls"] or 0)
    balance = engine.ledger.get_balance()
    detail = f"{n} entries, balance ${balance:,.2f}"
    if nulls:
        return node("ledger", "Ledger", "fail", detail + f"; {nulls} entries without an amount",
                    "Ledger rows are append-only; restore the last good backup (`kompany backup restore`).")
    if n and abs(total - balance) > 0.01:
        return node("ledger", "Ledger", "fail", detail + f"; sum of amounts ${total:,.2f} ≠ running balance",
                    "Running balance diverged from the entries — restore the last good backup or run `kompany merge --dry-run` to inspect.")
    return node("ledger", "Ledger", "ok", detail)


def check_artifact_workspace(engine: Any) -> dict[str, Any]:
    from kompany.core.artifact_evolution.workspace import ArtifactWorkspace

    ws = ArtifactWorkspace(engine.settings.data_dir)
    if not ws.exists():
        return node("artifacts", "Artifact workspace", "info", f"not initialised ({ws.root})")
    st = ws.status()
    detail = (f"{len(st['souls'])} souls, {len(st['workflows'])} workflows, {len(st['plugins'])} plugin scaffolds"
              f", head {str(st['head'])[:7]}")
    if st["dirty"]:
        return node("artifacts", "Artifact workspace", "warn", detail + "; uncommitted changes",
                    "Every evolved artifact must be a commit — commit or discard the working-tree changes in "
                    f"{ws.root}.")
    return node("artifacts", "Artifact workspace", "ok", detail)


__all__ = ["check_artifact_workspace", "check_ledger", "check_plugins", "check_souls", "check_workflows"]
