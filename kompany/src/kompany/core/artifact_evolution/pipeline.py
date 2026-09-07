"""Propose → apply → doctor → auto-revert (08-29 R2).

One cheap structured LLM call turns an instruction (plus the current YAML,
if any) into a complete new soul/workflow YAML. The engine then validates
it (reserved roles, no shadowing, schema), writes it into the workspace as
ONE git commit, runs the doctor self-test, and reverts the commit when the
doctor fails. Founder oversight is post-hoc: every outcome and every
privilege flag lands in the audit log and a notification.

Budget: a daily cap on this lane's spend (`artifact_evolution_daily_cap_usd`)
halts new proposals; each call's cost books through the LLM client exactly
like any other call (cost-as-expense).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from kompany.core.artifact_evolution.tiers import (
    ArtifactRejected,
    parse_yaml,
    privilege_flags,
    validate_soul,
    validate_workflow,
)
from kompany.core.artifact_evolution.workspace import ArtifactWorkspace
from kompany.core.run_context import current_run_id

ACTION_TYPE = "artifact_evolution"


class EvolvedArtifact(BaseModel):
    yaml_text: str = Field(description="The COMPLETE new YAML document (not a diff)")
    summary: str = Field(description="One sentence: what changed")
    rationale: str = Field(default="", description="Why this change serves the instruction")


_SYSTEM = (
    "You evolve Kompany artifacts. You receive an instruction and, when it exists, the current YAML of a "
    "soul (agent persona) or a workflow (multi-step recipe). Return the COMPLETE new YAML document.\n"
    "Rules: keep the same role / workflow_id; never use a reserved Core role (ceo, cfo, cto, cpo, cmo, cro, coo, "
    "csa, ciso, cos, cv, analyst, builder, procurement, researcher, writer) unless it is a workflow step's "
    "agent_role; do not widen allowed_tools unless the instruction explicitly asks; workflows use only "
    "prompt_template steps (no python_callable) with agent_role, cost_estimate_usd and autonomy_tier; keep "
    "prompts terse and specific. Output valid YAML only inside yaml_text."
)


def propose_artifact_evolution(engine: Any, kind: str, target: str, instruction: str) -> dict[str, Any]:
    """Run the full lane; never raises for expected failures (row carries the outcome)."""
    settings = engine.settings
    store = engine.artifact_proposals
    target = _normalise_target(target)
    pid = store.create(kind, target, instruction, run_id=current_run_id())

    if not bool(getattr(settings, "artifact_evolution_enabled", True)):
        return store.update(pid, status="failed", error="artifact evolution disabled (artifact_evolution_enabled=false)") or {}
    cap = float(getattr(settings, "artifact_evolution_daily_cap_usd", 2.0))
    spent = store.spent_today_usd()
    if spent >= cap:
        engine.audit.record(f"{ACTION_TYPE}.budget_halt", f"Artifact evolution halted: ${spent:.2f} ≥ daily cap ${cap:.2f}",
                            detail={"proposal_id": pid, "spent_today_usd": spent, "cap_usd": cap})
        return store.update(pid, status="failed", error=f"daily cap reached (${spent:.2f} of ${cap:.2f})") or {}

    if kind == "plugin":  # R4: scaffold → extension layer → approval card
        from kompany.core.artifact_evolution.incubation import incubate_plugin

        return incubate_plugin(engine, pid, target, instruction)

    ws = ArtifactWorkspace(settings.data_dir); ws.ensure()
    path = (ws.souls if kind == "soul" else ws.workflows) / target
    existing_text = path.read_text(encoding="utf-8") if path.is_file() else None
    existing = parse_yaml(existing_text) if existing_text else None

    # --- 1. propose (one structured LLM call, cost booked by the client) ---
    try:
        model = settings.get_model_for_tier(str(getattr(settings, "artifact_evolution_model_tier", "economy")))
        resp = engine.llm.call_structured(
            model=model, system=_SYSTEM, prompt=_prompt(kind, target, instruction, existing_text),
            output_schema=EvolvedArtifact, agent_name="artifact_evolution", action_type=ACTION_TYPE,
        )
        proposal: EvolvedArtifact = resp.parsed
        cost = float(getattr(resp, "cost_usd", 0.0) or 0.0)
    except Exception as exc:  # noqa: BLE001 — surfaced on the row, never raised
        return store.update(pid, status="failed", error=f"llm: {type(exc).__name__}: {exc}") or {}
    store.update(pid, cost_usd=cost, summary=proposal.summary, rationale=proposal.rationale)

    # --- 2. validate + flag ---
    try:
        data = parse_yaml(proposal.yaml_text)
        taken_roles, taken_ids = _taken(engine, exclude_path=path)
        if kind == "soul":
            validate_soul(data, target, taken_roles=taken_roles, existing=existing)
        else:
            validate_workflow(data, target, taken_ids=taken_ids, existing=existing)
    except ArtifactRejected as exc:
        engine.audit.record(f"{ACTION_TYPE}.rejected", f"Artifact proposal {pid} rejected: {exc}",
                            detail={"proposal_id": pid, "kind": kind, "target": target, "reason": str(exc)})
        return store.update(pid, status="rejected", error=str(exc)) or {}
    flags = privilege_flags(kind, existing, data)

    # --- 3. apply as one commit ---
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    try:
        sha = ws.commit(f"evolve({kind}): {target} — {proposal.summary[:60]} [{pid}]")
    except RuntimeError as exc:
        return store.update(pid, status="failed", error=str(exc)) or {}
    if sha is None:
        return store.update(pid, status="failed", error="proposal produced no change") or {}
    diff_stat = _diff_stat(ws, sha)
    store.update(pid, commit_sha=sha, diff_stat=diff_stat, flags=flags)

    # --- 4. doctor gate → auto-revert ---
    report = engine.doctor()
    failing = [n["id"] for n in _flatten(report) if n["status"] == "fail" and n["id"] not in ("kompany", "llm")]
    if failing:
        revert_sha = ws.revert(sha, reason=f"doctor failed: {', '.join(failing)}")
        row = store.update(pid, status="reverted", revert_sha=revert_sha, doctor_status="fail",
                           error=f"doctor failed on {', '.join(failing)}") or {}
        engine.audit.record(f"{ACTION_TYPE}.reverted", f"Artifact proposal {pid} reverted — doctor failed: {failing}",
                            detail={"proposal_id": pid, "kind": kind, "target": target, "commit": sha,
                                    "revert": revert_sha, "failing": failing, "cost_usd": cost})
        _notify(engine, f"Self-evolution reverted: {kind} {target} ({', '.join(failing)})", "warning", pid)
        engine.doctor()  # clear the doctor_failed event now that the revert restored the tree
        return row
    row = store.update(pid, status="applied", doctor_status="ok") or {}
    engine.audit.record(f"{ACTION_TYPE}.applied", f"Artifact proposal {pid} applied: {proposal.summary}",
                        detail={"proposal_id": pid, "kind": kind, "target": target, "commit": sha,
                                "diff_stat": diff_stat, "flags": flags, "cost_usd": cost, "new": existing is None})
    for flag in flags:
        engine.audit.record(f"{ACTION_TYPE}.privilege_flag", f"Artifact {target}: {flag['kind']} ({flag.get('severity')})",
                            detail={"proposal_id": pid, "kind": kind, "target": target, **flag})
    _notify(engine, f"Self-evolution applied: {kind} {target} — {proposal.summary}"
            + (f" ⚠ {len(flags)} privilege flag(s)" if flags else ""), "warning" if flags else "info", pid)
    return row


def revert_artifact_proposal(engine: Any, pid: str, reason: str = "founder revert") -> dict[str, Any] | None:
    """Post-hoc founder undo of an applied proposal (git revert of its commit)."""
    store = engine.artifact_proposals
    row = store.get(pid)
    if row is None:
        return None
    if row["status"] != "applied" or not row.get("commit_sha"):
        return row
    ws = ArtifactWorkspace(engine.settings.data_dir)
    revert_sha = ws.revert(row["commit_sha"], reason=reason)
    row = store.update(pid, status="reverted", revert_sha=revert_sha, error=reason) or row
    if row.get("kind") == "plugin":
        try:
            t = str(row["target"])
            engine.extension_remove(t[:-5] if t.endswith(".yaml") else t)
        except Exception:  # noqa: BLE001 — the scaffold is already reverted
            pass
    engine.audit.record(f"{ACTION_TYPE}.reverted", f"Artifact proposal {pid} reverted by founder: {reason}",
                        detail={"proposal_id": pid, "commit": row.get("commit_sha"), "revert": revert_sha, "reason": reason})
    engine.doctor()
    return row


# ---------------------------------------------------------------------------

def _normalise_target(target: str) -> str:
    """``growth-hacker`` / ``growth-hacker.yaml`` → ``growth-hacker.yaml``.
    Paths, dots and anything but a lowercase slug are refused."""
    import re

    name = target.strip()
    if name.endswith(".yaml"):
        name = name[:-5]
    if not re.match(r"^[a-z][a-z0-9_.-]{1,63}$", name):
        raise ValueError(f"target must be a lowercase slug such as growth-hacker (got {target!r})")
    return name + ".yaml"


def _prompt(kind: str, target: str, instruction: str, existing: str | None) -> str:
    head = f"Artifact kind: {kind}\nFile: {target}\nInstruction: {instruction}\n\n"
    if existing:
        return head + f"Current YAML:\n```yaml\n{existing}\n```\nReturn the complete evolved YAML."
    hint = ("Required soul fields: role (must equal the file stem), display_name, squad, model_tier, personality, "
            "traits, allowed_tools (list, keep minimal)." if kind == "soul" else
            "Required workflow fields: workflow_id (must equal the file stem), display_name, description, optional "
            "inputs list, steps (id, agent_role, cost_estimate_usd, autonomy_tier, prompt_template).")
    return head + f"No current file — create it. {hint}"


def _taken(engine: Any, *, exclude_path) -> tuple[set[str], set[str]]:
    from kompany.plugins.loader import discover
    from kompany.core.workflows_registry import _builtin_yaml_paths, _load_builtin

    found = discover(engine.settings.data_dir)
    roles = {getattr(s, "role", "") for s in found.get("soul", [])
             if str(getattr(s, "soul_yaml", "") or "") != str(exclude_path)}
    ids = {getattr(w, "workflow_id", "") for w in found.get("workflow", [])
           if str(getattr(w, "yaml_path", "") or "") != str(exclude_path)}
    for p in _builtin_yaml_paths():
        wf = _load_builtin(p)
        if wf is not None:
            ids.add(wf.workflow_id)
    return roles, ids


def _diff_stat(ws: ArtifactWorkspace, sha: str) -> str:
    p = ws._git("show", "--stat", "--format=", sha)
    return p.stdout.strip()[-500:] if p.returncode == 0 else ""


def _flatten(n: dict[str, Any]) -> list[dict[str, Any]]:
    out = [n]
    for c in n.get("children", []):
        out.extend(_flatten(c))
    return out


def _notify(engine: Any, summary: str, severity: str, pid: str) -> None:
    try:
        engine.dispatch_notifications([{"summary": summary, "severity": severity, "proposal_id": pid,
                                        "kind": ACTION_TYPE}])
    except Exception:  # noqa: BLE001 — notification is best-effort
        pass


__all__ = ["ACTION_TYPE", "EvolvedArtifact", "propose_artifact_evolution", "revert_artifact_proposal"]
