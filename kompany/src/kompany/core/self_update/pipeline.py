"""Propose flow for governed self-modification (PRD D2–D5).

``propose_self_update`` is the single safe propose path: clone → fresh
branch → harness session (existing L3 vehicles, self-update caps) →
post-session tier check on the REAL diff → tests inside the clone →
``self_update_proposal`` approval card. Approve/reject effects (push,
``gh pr create``) are PR2 — nothing here touches origin.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from kompany.core.event_hub import get_event_hub
from kompany.core.harness import HarnessCaps, HarnessEvent, HarnessResult
from kompany.core.harness.safety import assert_workspace_safe
from kompany.core.harness_execution.selection import harness_model, select_runner
from kompany.core.self_update.tiers import T3_PROMPT_NOTE, classify_paths
from kompany.core.self_update.workspace import (
    commit_all,
    diff_stats,
    discard_branch,
    ensure_clone,
    start_branch,
)
from kompany.state.models import ApprovalRequest
from kompany.state.self_update_proposals import SelfUpdateProposalStore

# How many trailing test-output lines survive into the card.
_TEST_TAIL_LINES = 30
# Hard wall-clock ceiling on the clone test run (seconds).
_TEST_TIMEOUT_SECONDS = 900
# Cap on the files list embedded in the approval card payload.
_CARD_FILES_LIMIT = 20


def propose_self_update(engine: Any, instruction: str) -> dict:
    """Run the full propose flow; returns the final proposal row dict.

    Never raises for expected failure modes (no vehicle, dead session,
    T3 diff) — the proposal row's ``status`` carries the outcome.
    """
    store = _store(engine)
    settings = engine.settings
    proposal_id = store.create(instruction)
    proposal = store.get(proposal_id)
    assert proposal is not None  # just created
    branch = proposal["branch"]

    clone = ensure_clone(settings.data_dir)
    start_branch(clone, branch)
    # Belt-and-braces: the clone is outside the running tree by
    # construction; the constitution guard must agree.
    assert_workspace_safe(clone)

    runner = select_runner(
        settings,
        health_events=getattr(engine, "health_events", None),
        permission_mode="acceptEdits",
    )
    if runner is None:
        return store.update(
            proposal_id,
            status="failed",
            test_summary="failed: no vehicle available (ModelSource unset, "
            "flag off, or CLI missing)",
        ) or {}
    store.update(proposal_id, vehicle=runner.vehicle_name)

    caps = HarnessCaps(
        budget_cap_usd=float(settings.self_update_budget_cap_usd),
        max_turns=int(settings.self_update_max_turns),
    )
    result = _run_session(engine, runner, _build_prompt(instruction), clone, caps)
    cost = _book_cost(engine, settings, runner.vehicle_name, proposal_id, result)
    store.update(
        proposal_id, session_id=result.session_id, cost_usd=float(cost or 0.0)
    )

    commit_all(clone, f"Self-update: {instruction[:72]}")
    files, diff_stat = diff_stats(clone)
    if not files:
        # Zero-diff proposals are noise regardless of exit status — a
        # successful session that changed nothing has nothing to approve
        # (live finding: permission-denied writes produced exit success
        # with an empty diff and still filed a card).
        discard_branch(clone, branch)
        return store.update(
            proposal_id,
            status="failed",
            test_summary=(
                "failed: session produced no changes"
                + (f" ({result.error or result.exit_status})"
                   if result.exit_status != "success" else "")
            ),
        ) or {}

    tier, t3_hits = classify_paths(files)
    if tier == "t3":
        discard_branch(clone, branch)
        _record_t3_blocked(engine, proposal_id, branch, t3_hits)
        return store.update(
            proposal_id,
            status="aborted_t3",
            tier="t3",
            files_changed=files,
            diff_stat=diff_stat,
            test_summary=f"aborted: diff touched protected paths {t3_hits}",
        ) or {}

    test_summary = _run_tests(clone, settings)

    summary = (
        f"Self-update proposal ({tier}): {instruction[:120]} — "
        f"{len(files)} file(s), tests {'PASSED' if test_summary.startswith('PASSED') else 'FAILED'}"
    )
    request = ApprovalRequest(
        action_type="self_update_proposal",
        summary=summary,
        payload={
            "proposal_id": proposal_id,
            "branch": branch,
            "tier": tier,
            "diff_stat": diff_stat,
            "files": files[:_CARD_FILES_LIMIT],
            "test_summary": test_summary,
            "instruction": instruction,
            "cost_usd": float(cost or 0.0),
        },
        requested_by="self_update_pipeline",
        severity="high" if tier == "t2" else "medium",
    )
    engine.approvals.create(request)
    return store.update(
        proposal_id,
        status="proposed",
        tier=tier,
        files_changed=files,
        diff_stat=diff_stat,
        test_summary=test_summary,
        approval_id=request.id,
    ) or {}


def _store(engine: Any) -> SelfUpdateProposalStore:
    existing = getattr(engine, "self_update_proposals", None)
    if existing is not None:
        return existing
    return SelfUpdateProposalStore(engine.db)


def _build_prompt(instruction: str) -> str:
    return (
        "You are performing a governed self-update on the Kompany "
        "codebase. Work ONLY inside this repository checkout — never "
        "touch files outside it.\n\n"
        f"Instruction:\n{instruction}\n\n"
        f"{T3_PROMPT_NOTE}\n\n"
        "MANDATORY REGRESSION TEST: any behavior change must come with a "
        "new or updated test under kompany/tests/. A change without test "
        "coverage will be rejected by the founder.\n"
    )


def _run_session(
    engine: Any,
    runner: Any,
    prompt: str,
    clone: Path,
    caps: HarnessCaps,
) -> HarnessResult:
    """Run the vehicle session; a crashed vehicle becomes an error result."""
    hub = getattr(engine, "event_hub", None) or get_event_hub()

    def on_event(event: HarnessEvent) -> None:
        try:
            hub.publish(
                "self_update.event",
                {
                    "activity_kind": "self_update",
                    "kind": event.kind,
                    "summary": _summarize(event),
                },
            )
        except Exception:  # noqa: BLE001 — live feed is best-effort
            pass

    try:
        return runner.start(prompt, clone, caps, on_event)
    except Exception as exc:  # noqa: BLE001 — outcome captured on the row
        return HarnessResult(exit_status="error", error=str(exc))


def _summarize(event: HarnessEvent) -> str:
    if event.kind == "text":
        return str(event.payload.get("text") or "")[:120]
    if event.kind == "tool_use":
        return str(event.payload.get("tool_name") or "")[:120]
    return event.kind


def _book_cost(
    engine: Any,
    settings: Any,
    vehicle: str,
    proposal_id: str,
    result: HarnessResult,
) -> float:
    """Book the session spend via the ONLY approved harness cost path."""
    return engine.cost_tracker.record_external(
        model=harness_model(settings, vehicle),
        cost_usd=result.cost_usd or 0.0,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        description=f"Self-update session {proposal_id}",
        run_id=None,
        is_estimate=result.cost_is_estimate,
    )


def _record_t3_blocked(
    engine: Any, proposal_id: str, branch: str, t3_hits: list[str]
) -> None:
    health = getattr(engine, "health_events", None)
    if health is None:
        return
    try:
        health.record(
            "self_update_t3_blocked",
            detail={
                "proposal_id": proposal_id,
                "branch": branch,
                "t3_paths": t3_hits,
            },
        )
    except Exception:  # noqa: BLE001 — abort bookkeeping must not crash
        pass


def _run_tests(clone: Path, settings: Any) -> str:
    """Run the configured test command inside the clone; return a summary.

    Summary starts with ``PASSED`` or ``FAILED`` followed by the last
    ``_TEST_TAIL_LINES`` lines of combined output (honest-assessment:
    red tests still get filed, prominently marked — PRD D4).
    """
    cmd = shlex.split(str(settings.self_update_test_cmd))
    if cmd and cmd[0] in ("python", "python3"):
        cmd[0] = sys.executable
    pkg_dir = clone / "kompany"
    cwd = pkg_dir if (pkg_dir / "tests").exists() else clone
    env = dict(os.environ)
    src = str(pkg_dir / "src")
    env["PYTHONPATH"] = (
        src + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else src
    )
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=_TEST_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"FAILED: test command could not complete ({exc})"
    output = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(output.strip().splitlines()[-_TEST_TAIL_LINES:])
    verdict = "PASSED" if proc.returncode == 0 else "FAILED"
    return f"{verdict}\n{tail}".strip()


__all__ = ["propose_self_update"]
