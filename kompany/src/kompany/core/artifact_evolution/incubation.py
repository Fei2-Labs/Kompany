"""Plugin self-incubation (08-29 R4).

A directive becomes a runnable **extension package** — `extension.json`
manifest + stdlib-only `main.py` exposing ``run(job, host)`` + optional soul
YAML — scaffolded into ``<data_dir>/artifacts/plugins/<id>/`` as one commit,
then handed to the customer extension layer (``engine.extension_install``),
which copies it into ``<data_dir>/extensions/``, checks the Core range and
files the ``extension_activate`` approval card. So incubated code never runs
until the founder approves, and then only in the isolated worker with the
capabilities the manifest declares. The scaffold itself is auditable and
revertible like every other workspace artifact.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from kompany.core.artifact_evolution.tiers import ArtifactRejected, parse_yaml, validate_soul
from kompany.core.artifact_evolution.workspace import ArtifactWorkspace
from kompany.core.extensions.manifest import ExtensionManifest, ManifestError

ACTION_TYPE = "artifact_evolution"
_FORBIDDEN_IMPORTS = {"subprocess", "socket", "ctypes", "multiprocessing", "importlib", "os", "shutil", "pathlib", "sys"}
_MAX_MAIN_BYTES = 40_000


class IncubatedPlugin(BaseModel):
    """What the LLM returns: a complete, self-contained extension."""

    name: str = Field(description="Human name")
    description: str = Field(default="", description="One paragraph: what it does")
    version: str = Field(default="0.1.0")
    core_api: str = Field(default="", description="Core version range like >=0.1,<0.3 or empty")
    tools: list[str] = Field(default_factory=list, description="Engine tools it will call through host.tool")
    paths: list[str] = Field(default_factory=list, description="Relative data-dir paths it needs, e.g. notes/")
    network: list[str] = Field(default_factory=list, description="Hostnames it fetches through host.fetch")
    credentials: list[str] = Field(default_factory=list, description="Credential connectors it leases")
    budget_usd: float = Field(default=0.0, ge=0.0)
    main_py: str = Field(description="Python source defining run(job, host); stdlib only, no os/sys/subprocess")
    soul_yaml: str = Field(default="", description="Optional soul YAML for a new role that uses this extension")
    summary: str = Field(description="One sentence: what was scaffolded")


_SYSTEM = (
    "You incubate Kompany extensions. Return a complete extension: a manifest (name, version, core_api, and ONLY the "
    "capabilities the code actually uses: tools, paths, network, credentials, budget_usd) plus main_py — Python "
    "source defining run(job, host) that returns a JSON-serialisable value. The code runs in an isolated process "
    "with the standard library only: never import os, sys, subprocess, socket, shutil, pathlib, importlib or "
    "ctypes; do all I/O through host.read/host.write (declared paths), host.fetch (declared hosts), host.tool "
    "(declared engine tools), host.credential (declared connectors), host.log. Keep it small and testable. "
    "Optionally include soul_yaml for a NEW role (never a reserved Core role) that will use this extension."
)


def incubate_plugin(engine: Any, pid: str, target: str, instruction: str) -> dict[str, Any]:
    """Run the incubation flow for proposal ``pid``; the row carries the outcome."""
    settings, store = engine.settings, engine.artifact_proposals
    ext_id = target[:-5] if target.endswith(".yaml") else target  # dotted ids: never Path.stem
    ws = ArtifactWorkspace(settings.data_dir); ws.ensure()
    scaffold = ws.plugins / ext_id
    existing_manifest = None
    if (scaffold / "extension.json").is_file():
        try:
            existing_manifest = json.loads((scaffold / "extension.json").read_text(encoding="utf-8"))
        except ValueError:
            existing_manifest = None

    # --- 1. propose ---------------------------------------------------------
    try:
        model = settings.get_model_for_tier(str(getattr(settings, "artifact_evolution_model_tier", "economy")))
        resp = engine.llm.call_structured(
            model=model, system=_SYSTEM, prompt=_prompt(ext_id, instruction, scaffold, existing_manifest),
            output_schema=IncubatedPlugin, agent_name="artifact_evolution", action_type=ACTION_TYPE, max_tokens=8192,
        )
        plan: IncubatedPlugin = resp.parsed
        cost = float(getattr(resp, "cost_usd", 0.0) or 0.0)
    except Exception as exc:  # noqa: BLE001
        return store.update(pid, status="failed", error=f"llm: {type(exc).__name__}: {exc}") or {}
    store.update(pid, cost_usd=cost, summary=plan.summary, rationale=plan.description[:500])

    # --- 2. validate (nothing written yet) ----------------------------------
    try:
        manifest = _manifest_for(ext_id, plan, existing_manifest)
        _check_source(plan.main_py, manifest)
        soul_data = None
        if plan.soul_yaml.strip():
            soul_data = parse_yaml(plan.soul_yaml)
            taken = {getattr(s, "role", "") for s in _discovered_souls(engine)}
            soul_path = ws.souls / f"{soul_data.get('role')}.yaml"
            existing_soul = parse_yaml(soul_path.read_text(encoding="utf-8")) if soul_path.is_file() else None
            if existing_soul is not None:
                taken.discard(str(soul_data.get("role")))
            validate_soul(soul_data, soul_path.name, taken_roles=taken, existing=existing_soul)
    except (ArtifactRejected, ManifestError, ValueError) as exc:
        engine.audit.record(f"{ACTION_TYPE}.rejected", f"Plugin incubation {pid} rejected: {exc}",
                            detail={"proposal_id": pid, "kind": "plugin", "target": ext_id, "reason": str(exc)})
        return store.update(pid, status="rejected", error=str(exc)) or {}
    flags = _flags(existing_manifest, manifest)

    # --- 3. scaffold as one commit ------------------------------------------
    scaffold.mkdir(parents=True, exist_ok=True)
    (scaffold / "extension.json").write_text(json.dumps(manifest.model_dump(exclude_none=True), indent=2) + "\n", encoding="utf-8")
    (scaffold / manifest.entrypoint).write_text(plan.main_py.rstrip("\n") + "\n", encoding="utf-8")
    (scaffold / "README.md").write_text(f"# {manifest.name}\n\n{plan.description}\n\nIncubated by Kompany "
                                        f"(proposal {pid}) from: {instruction}\n", encoding="utf-8")
    if soul_data is not None:
        (ws.souls / f"{soul_data['role']}.yaml").write_text(yaml.safe_dump(soul_data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    try:
        sha = ws.commit(f"incubate(plugin): {ext_id} — {plan.summary[:60]} [{pid}]")
    except RuntimeError as exc:
        return store.update(pid, status="failed", error=str(exc)) or {}
    if sha is None:
        return store.update(pid, status="failed", error="incubation produced no change") or {}
    store.update(pid, commit_sha=sha, flags=flags, diff_stat=_diff_stat(ws, sha), target=ext_id)

    # --- 4. hand to the extension layer (approval card) + doctor gate --------
    try:
        ext_row = engine.extension_install(scaffold)
    except Exception as exc:  # noqa: BLE001 — install refusal → revert the scaffold
        revert_sha = ws.revert(sha, reason=f"extension install refused: {exc}")
        engine.audit.record(f"{ACTION_TYPE}.reverted", f"Plugin incubation {pid} reverted — install refused: {exc}",
                            detail={"proposal_id": pid, "target": ext_id, "commit": sha, "revert": revert_sha})
        return store.update(pid, status="reverted", revert_sha=revert_sha, error=f"extension install refused: {exc}") or {}
    from kompany.core.doctor import gate_failures

    report = engine.doctor()
    failing = gate_failures(report)
    if failing:
        revert_sha = ws.revert(sha, reason=f"doctor failed: {', '.join(failing)}")
        engine.extension_remove(ext_id)
        engine.audit.record(f"{ACTION_TYPE}.reverted", f"Plugin incubation {pid} reverted — doctor failed: {failing}",
                            detail={"proposal_id": pid, "target": ext_id, "commit": sha, "revert": revert_sha, "failing": failing})
        engine.doctor()
        return store.update(pid, status="reverted", revert_sha=revert_sha, doctor_status="fail",
                            error=f"doctor failed on {', '.join(failing)}") or {}
    row = store.update(pid, status="applied", doctor_status="ok") or {}
    row["extension"] = {"id": ext_row["id"], "status": ext_row["status"], "approval_id": ext_row.get("approval_id"),
                        "block_reason": ext_row.get("block_reason")}
    engine.audit.record(f"{ACTION_TYPE}.incubated", f"Plugin {ext_id} incubated ({ext_row['status']}): {plan.summary}",
                        detail={"proposal_id": pid, "target": ext_id, "commit": sha, "approval_id": ext_row.get("approval_id"),
                                "capabilities": manifest.capabilities.model_dump(), "flags": flags, "cost_usd": cost,
                                "soul": soul_data.get("role") if soul_data else None})
    for flag in flags:
        engine.audit.record(f"{ACTION_TYPE}.privilege_flag", f"Plugin {ext_id}: {flag['kind']} ({flag.get('severity')})",
                            detail={"proposal_id": pid, "kind": "plugin", "target": ext_id, **flag})
    try:
        engine.dispatch_notifications([{"summary": f"Self-evolution incubated extension {ext_id} — approve its activation "
                                                   f"card to run it" + (f" ⚠ {len(flags)} flag(s)" if flags else ""),
                                        "severity": "warning" if flags else "info", "proposal_id": pid, "kind": ACTION_TYPE}])
    except Exception:  # noqa: BLE001
        pass
    return row


# ---------------------------------------------------------------------------

def _manifest_for(ext_id: str, plan: IncubatedPlugin, existing: dict[str, Any] | None) -> ExtensionManifest:
    data = {
        "id": ext_id, "name": plan.name or ext_id, "version": plan.version or "0.1.0", "owner": "customer",
        "origin": "incubated", "entrypoint": "main.py", "core_api": plan.core_api or (existing or {}).get("core_api", ""),
        "capabilities": {"tools": plan.tools, "paths": plan.paths, "network": plan.network,
                         "credentials": plan.credentials, "budget_usd": plan.budget_usd},
        "description": plan.description[:500],
    }
    if existing and existing.get("version") == data["version"]:
        raise ArtifactRejected(f"version {data['version']} already scaffolded — bump the version to evolve {ext_id}")
    try:
        return ExtensionManifest.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise ArtifactRejected(f"manifest invalid: {exc}") from exc


def _check_source(source: str, manifest: ExtensionManifest) -> None:
    if len(source.encode()) > _MAX_MAIN_BYTES:
        raise ArtifactRejected("main.py exceeds the 40 KB incubation limit")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ArtifactRejected(f"main.py does not parse: {exc.msg} (line {exc.lineno})") from exc
    has_run = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run" and len(n.args.args) >= 2
                  for n in tree.body)
    if not has_run:
        raise ArtifactRejected("main.py must define run(job, host) at module level")
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        bad = [n for n in names if n in _FORBIDDEN_IMPORTS]
        if bad:
            raise ArtifactRejected(f"main.py imports {', '.join(bad)} — use the host capabilities instead")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval", "open", "__import__"):
            raise ArtifactRejected(f"main.py calls {node.func.id}() — not allowed in an incubated extension")


def _flags(old: dict[str, Any] | None, new: ExtensionManifest) -> list[dict[str, Any]]:
    caps = new.capabilities
    flags: list[dict[str, Any]] = []
    before = (old or {}).get("capabilities") or {}
    for key in ("tools", "network", "credentials"):
        added = [x for x in getattr(caps, key) if x not in (before.get(key) or [])]
        if added:
            flags.append({"kind": f"capability_{key}", "added": added,
                          "severity": "high" if key == "credentials" else "medium"})
    if caps.budget_usd > float(before.get("budget_usd") or 0.0):
        flags.append({"kind": "budget_increase", "from": float(before.get("budget_usd") or 0.0), "to": caps.budget_usd,
                      "severity": "medium"})
    return flags


def _prompt(ext_id: str, instruction: str, scaffold: Path, existing: dict[str, Any] | None) -> str:
    head = f"Extension id: {ext_id}\nInstruction: {instruction}\n\n"
    if existing and (scaffold / str(existing.get("entrypoint", "main.py"))).is_file():
        code = (scaffold / str(existing.get("entrypoint", "main.py"))).read_text(encoding="utf-8")
        return head + (f"Current manifest:\n```json\n{json.dumps(existing, indent=2)}\n```\nCurrent main.py:\n```python\n{code}\n```\n"
                       "Return the complete evolved extension with a bumped version.")
    return head + "No current extension — create it from scratch."


def _discovered_souls(engine: Any) -> list[Any]:
    try:
        from kompany.plugins.loader import discover

        return discover(engine.settings.data_dir).get("soul", [])
    except Exception:  # noqa: BLE001
        return []


def _diff_stat(ws: ArtifactWorkspace, sha: str) -> str:
    p = ws._git("show", "--stat", "--format=", sha)
    return p.stdout.strip()[-500:] if p.returncode == 0 else ""


def _flatten(n: dict[str, Any]) -> list[dict[str, Any]]:
    out = [n]
    for c in n.get("children", []):
        out.extend(_flatten(c))
    return out


__all__ = ["IncubatedPlugin", "incubate_plugin"]
