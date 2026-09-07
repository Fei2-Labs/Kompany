"""Validation + privilege-flag detection for evolved artifacts (08-29 R2).

The artifact lane is full-auto, so these checks are the only gate before a
commit: they refuse anything that would shadow Core/Pro, anything that is
not a valid soul / workflow, and they flag (loudly, never silently) any
change that widens what a role may do.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

import yaml

from kompany.agents.soul_agent import _RESERVED_CORE_ROLES, _REQUIRED_FIELDS

_SAFE_NAME = r"^[a-z][a-z0-9_-]{1,63}$"


class ArtifactRejected(ValueError):
    """The proposal cannot be applied; nothing was written."""


def parse_yaml(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ArtifactRejected(f"not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ArtifactRejected("artifact YAML must be a mapping")
    return data


def validate_soul(data: dict[str, Any], target: str, *, taken_roles: set[str], existing: dict[str, Any] | None) -> None:
    for field in _REQUIRED_FIELDS:
        if not data.get(field):
            raise ArtifactRejected(f"soul missing required field {field!r}")
    role = str(data["role"])
    if role in _RESERVED_CORE_ROLES:
        raise ArtifactRejected(f"role {role!r} is a reserved Core role — evolved souls must add NEW roles (ADR-0001)")
    if role in taken_roles and not (existing and existing.get("role") == role):
        raise ArtifactRejected(f"role {role!r} already provided by a builtin/Pro/workspace soul")
    if existing and existing.get("role") != role:
        raise ArtifactRejected(f"an evolved soul may not change its role ({existing.get('role')!r} → {role!r})")
    if Path(target).stem != role:
        raise ArtifactRejected(f"soul file name must equal its role ({role}.yaml)")
    tools = data.get("allowed_tools")
    if tools is not None and (not isinstance(tools, list) or not all(isinstance(t, str) for t in tools)):
        raise ArtifactRejected("allowed_tools must be a list of glob strings")


def validate_workflow(data: dict[str, Any], target: str, *, taken_ids: set[str], existing: dict[str, Any] | None) -> None:
    from kompany.core.workflow_runner import WorkflowRunner, WorkflowYAMLInvalid

    try:
        WorkflowRunner(dict(data))
    except WorkflowYAMLInvalid as exc:
        raise ArtifactRejected(f"invalid workflow: {exc}") from exc
    wid = str(data["workflow_id"])
    if wid in taken_ids and not (existing and existing.get("workflow_id") == wid):
        raise ArtifactRejected(f"workflow_id {wid!r} already provided by a builtin/Pro/workspace workflow")
    if existing and existing.get("workflow_id") != wid:
        raise ArtifactRejected(f"an evolved workflow may not change its id ({existing.get('workflow_id')!r} → {wid!r})")
    if Path(target).stem != wid:
        raise ArtifactRejected(f"workflow file name must equal its workflow_id ({wid}.yaml)")
    for step in data.get("steps", []):
        if step.get("python_callable"):
            raise ArtifactRejected("evolved workflows may not declare python_callable steps (no code on this lane)")
        if str(step.get("agent_role", "")) in _RESERVED_CORE_ROLES or not step.get("agent_role"):
            continue


def privilege_flags(kind: str, old: dict[str, Any] | None, new: dict[str, Any]) -> list[dict[str, Any]]:
    """Loud-but-not-blocking findings: widened tool access, new spend, new
    non-auto steps. Every flag lands in the audit feed."""
    flags: list[dict[str, Any]] = []
    if kind == "soul":
        before = [str(t) for t in (old or {}).get("allowed_tools") or []]
        after = [str(t) for t in new.get("allowed_tools") or []]
        widened = [t for t in after if not any(fnmatch.fnmatch(t, b) or t == b for b in before)]
        if widened and old is not None:
            flags.append({"kind": "allowed_tools_expansion", "added": widened, "before": before,
                          "severity": "high" if any("*" in t for t in widened) else "medium"})
        elif widened:  # brand-new role: its initial tool grant is visible, not alarming
            flags.append({"kind": "new_role_tools", "added": widened, "severity": "low"})
        if str(new.get("model_tier", "")) == "apex" and (old or {}).get("model_tier") != "apex":
            flags.append({"kind": "model_tier_apex", "severity": "medium"})
    else:
        old_steps = {s.get("id"): s for s in (old or {}).get("steps", [])}
        for step in new.get("steps", []):
            prev = old_steps.get(step.get("id"))
            tier = str(step.get("autonomy_tier", "auto"))
            if tier != "auto" and (prev is None or str(prev.get("autonomy_tier", "auto")) != tier):
                flags.append({"kind": "non_auto_step", "step": step.get("id"), "autonomy_tier": tier, "severity": "low"})
            est = float(step.get("cost_estimate_usd") or 0.0)
            prev_est = float((prev or {}).get("cost_estimate_usd") or 0.0)
            if est > max(prev_est * 2, 1.0):
                flags.append({"kind": "cost_estimate_jump", "step": step.get("id"), "from": prev_est, "to": est, "severity": "medium"})
            for tool in step.get("tools") or []:
                if prev is None or tool not in (prev.get("tools") or []):
                    flags.append({"kind": "new_step_tool", "step": step.get("id"), "tool": tool, "severity": "medium"})
    return flags


__all__ = ["ArtifactRejected", "parse_yaml", "privilege_flags", "validate_soul", "validate_workflow"]
