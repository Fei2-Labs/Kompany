"""Workspace artifacts as plugin-contract objects (loader tail).

Souls and workflows evolved into ``<data_dir>/artifacts/`` are plain YAML.
The loader wraps them in the same ABCs Pro plugins implement so the
registry, the workflow registry and the doctor treat all three sources —
builtin → Pro → workspace — through one interface. Workspace contributions
merge LAST and never replace a builtin or Pro id. ``plugins/`` scaffolds
are listed, never imported: executable code stays in the isolated
extension layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from kompany.plugins.contract import AgentSoul, CostEstimate, Workflow


class WorkspaceSoul(AgentSoul):
    def __init__(self, path: Path, data: dict[str, Any]):
        self.soul_yaml = path
        self.role = str(data.get("role") or "")
        self.display_name = str(data.get("display_name") or "")
        self.tier = str(data.get("tier") or "c_level")
        self.squad = str(data.get("squad") or "")
        self.origin = "workspace"


class WorkspaceWorkflow(Workflow):
    def __init__(self, path: Path, data: dict[str, Any]):
        self.yaml_path = path
        self.workflow_id = str(data.get("workflow_id") or "")
        self.display_name = str(data.get("display_name") or self.workflow_id)
        self.origin = "workspace"
        self._steps = data.get("steps") or []

    def estimate_cost(self) -> CostEstimate:
        total = 0.0
        for step in self._steps:
            try:
                total += float(step.get("cost_estimate_usd") or 0.0)
            except (TypeError, ValueError):
                continue
        return CostEstimate(llm_usd=total)


def load_workspace_contributions(root: Path, taken_roles: set[str], taken_workflows: set[str]
                                 ) -> tuple[list[WorkspaceSoul], list[WorkspaceWorkflow], list[tuple[str, str, str]]]:
    """(souls, workflows, errors). Validation reuses the soul loader's
    reserved-role guard so an evolved soul can never shadow a Core role."""
    from kompany.agents.soul_agent import _load_yaml

    souls: list[WorkspaceSoul] = []
    workflows: list[WorkspaceWorkflow] = []
    errors: list[tuple[str, str, str]] = []
    souls_dir, wf_dir = root / "souls", root / "workflows"
    if souls_dir.is_dir():
        for path in sorted(souls_dir.glob("*.yaml")):
            try:
                data = _load_yaml(path)
                if data["role"] in taken_roles:
                    raise ValueError(f"role {data['role']!r} already provided by builtin/Pro — workspace never replaces")
                souls.append(WorkspaceSoul(path, data)); taken_roles.add(data["role"])
            except Exception as exc:  # noqa: BLE001 — one bad file must not hide the rest
                errors.append(("workspace.souls", path.name, repr(exc)))
    if wf_dir.is_dir():
        for path in sorted(wf_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                wid = str(data.get("workflow_id") or "")
                if not wid or not isinstance(data.get("steps"), list):
                    raise ValueError("workflow YAML needs workflow_id and a steps list")
                if wid in taken_workflows:
                    raise ValueError(f"workflow_id {wid!r} already provided by builtin/Pro — workspace never replaces")
                workflows.append(WorkspaceWorkflow(path, data)); taken_workflows.add(wid)
            except Exception as exc:  # noqa: BLE001
                errors.append(("workspace.workflows", path.name, repr(exc)))
    return souls, workflows, errors


__all__ = ["WorkspaceSoul", "WorkspaceWorkflow", "load_workspace_contributions"]
