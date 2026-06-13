"""Collection helpers: each function queries DB rows and builds payload models.

All functions are pure in the sense that they make no writes.
"""

from __future__ import annotations

import json
from typing import Any

from kompany.state.approvals import ApprovalRequests
from kompany.state.database import Database
from kompany.state.episode_payload import (
    ApprovalComment,
    ApprovalEvent,
    AuditEvent,
    DecisionEntry,
    GlossaryDriftEntry,
    HealthEvent,
    LedgerSummary,
    LifecycleEvent,
    ReflectionEntry,
    TargetsBundleEntry,
    TargetsSnapshot,
    TaskEntry,
)
from kompany.state.health_events import HealthEvents
from kompany.state.targets import get_bundle as _get_targets_bundle

from ._constants import _KEY_AUDIT_EVENT_TYPES
from ._helpers import stringify_decision_summary


def collect_tasks(db: Database, project_id: str) -> list[TaskEntry]:
    rows = db.execute(
        "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    tasks: list[TaskEntry] = []
    for row in rows:
        lifecycle: list[LifecycleEvent] = []
        if row["completed_at"]:
            lifecycle.append(
                LifecycleEvent(
                    at=row["completed_at"],
                    state=row["status"],
                    reason=None,
                )
            )
        tasks.append(
            TaskEntry(
                id=row["id"],
                title=row["title"],
                assigned_agent=row["assigned_agent"],
                status=row["status"],
                result=row["result"],
                run_id=row["run_id"],
                lifecycle_events=lifecycle,
            )
        )
    return tasks


def collect_ledger_summary(
    db: Database,
    project_id: str,
    tasks: list[TaskEntry],
) -> LedgerSummary:
    rows = db.execute(
        "SELECT category, amount FROM ledger WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    total_income = 0.0
    total_expense = 0.0
    ai_cost = 0.0
    by_category: dict[str, float] = {}
    for row in rows:
        amount = float(row["amount"] or 0.0)
        category = row["category"]
        by_category[category] = by_category.get(category, 0.0) + amount
        if amount > 0:
            total_income += amount
        else:
            total_expense += amount
        if category == "ai_cost":
            ai_cost += amount

    # AI cost is reported as a positive magnitude (it's stored negative
    # because it's an expense, but the summary should be human-readable).
    return LedgerSummary(
        total_income=total_income,
        total_expense=total_expense,
        ai_cost=abs(ai_cost),
        by_category=by_category,
        by_agent={},
    )


def collect_decisions_and_debates(
    db: Database,
    project_id: str,
    triggering_directive_id: str | None,
) -> tuple[list[DecisionEntry], list[str]]:
    """Pull decisions that mention this project and aggregate debate ids.

    Decisions don't carry ``project_id`` directly (their ``result`` JSON
    does, when relevant), so we use two strategies:

    1. Decisions whose ``directive_id`` matches the project's triggering
       directive — exact link.
    2. Decisions whose ``result`` JSON mentions the project id — useful
       when a single strategic debate later spawned this project.

    Debate ids are pulled from ``debates.project_id`` *and* from any
    ``result.debate_id`` field surfaced in the matched decisions.
    """
    candidate_decisions: dict[str, Any] = {}

    if triggering_directive_id:
        for row in db.execute(
            "SELECT * FROM decisions WHERE directive_id = ? "
            "ORDER BY timestamp",
            (triggering_directive_id,),
        ).fetchall():
            candidate_decisions[row["id"]] = row

    # Pick up decisions whose result mentions the project id. The
    # ``result`` column is JSON-encoded text; LIKE is sufficient and
    # cheap for the volumes we care about.
    for row in db.execute(
        "SELECT * FROM decisions WHERE result LIKE ? ORDER BY timestamp",
        (f"%{project_id}%",),
    ).fetchall():
        candidate_decisions.setdefault(row["id"], row)

    decisions: list[DecisionEntry] = []
    debate_ids: set[str] = set()
    for row in candidate_decisions.values():
        try:
            result_payload = json.loads(row["result"]) if row["result"] else {}
        except (TypeError, ValueError):
            result_payload = {}
        try:
            agents = json.loads(row["agents_involved"])
            if not isinstance(agents, list):
                agents = []
        except (TypeError, ValueError):
            agents = []
        summary = stringify_decision_summary(result_payload)
        decisions.append(
            DecisionEntry(
                id=row["id"],
                directive_id=row["directive_id"],
                run_id=row["run_id"],
                summary=summary,
                agents_involved=agents,
            )
        )
        ref = result_payload.get("debate_id") if isinstance(result_payload, dict) else None
        if isinstance(ref, str) and ref:
            debate_ids.add(ref)

    # Debates linked directly to the project.
    for row in db.execute(
        "SELECT id FROM debates WHERE project_id = ?",
        (project_id,),
    ).fetchall():
        debate_ids.add(row["id"])

    return decisions, sorted(debate_ids)


def collect_audit_events(
    db: Database,
    project_id: str,
) -> tuple[list[AuditEvent], list[str]]:
    rows = db.execute(
        "SELECT * FROM audit_log WHERE project_id = ? "
        "ORDER BY id",
        (project_id,),
    ).fetchall()
    events: list[AuditEvent] = []
    run_ids: list[str] = []
    seen_run_ids: set[str] = set()
    for row in rows:
        if row["run_id"] and row["run_id"] not in seen_run_ids:
            seen_run_ids.add(row["run_id"])
            run_ids.append(row["run_id"])
        if row["event_type"] not in _KEY_AUDIT_EVENT_TYPES:
            continue
        detail: dict[str, Any] = {}
        if row["detail"]:
            try:
                parsed = json.loads(row["detail"])
                if isinstance(parsed, dict):
                    detail = parsed
                else:
                    detail = {"value": parsed}
            except (TypeError, ValueError):
                detail = {"raw": row["detail"]}
        events.append(
            AuditEvent(
                at=row["timestamp"],
                type=row["event_type"],
                run_id=row["run_id"],
                detail=detail,
            )
        )
    return events, run_ids


def collect_health_events(db: Database, project_id: str) -> list[HealthEvent]:
    """Pull resilience-watchdog events tied to this project.

    Built by ``05-18-resilience-foundation``. ``id`` / ``status`` /
    ``project_id`` / ``resolved_by`` / ``resolved_at`` / ``snoozed_until``
    are propagated into the payload so distillation can later learn
    from player resolutions, not just from raw warnings.
    """
    store = HealthEvents(db)
    rows = store.list_for_project(project_id)
    events: list[HealthEvent] = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        events.append(
            HealthEvent(
                at=row["created_at"],
                run_id=row.get("run_id"),
                kind=row["kind"],
                task_id=row.get("task_id"),
                detail=detail,
                id=row.get("id"),
                project_id=row.get("project_id") or project_id,
                status=row.get("status") or "open",
                resolved_by=row.get("resolved_by"),
                resolved_at=row.get("resolved_at"),
                snoozed_until=row.get("snoozed_until"),
            )
        )
    return events


def collect_approval_events(db: Database, project_id: str) -> list[ApprovalEvent]:
    """Materialize approvals + comments tied to this project.

    Built by ``05-18-approval-thread-and-rpg``. Each ``ApprovalEvent``
    carries the approval's outcome (``status``), all comments in the
    thread, and ``decided_at`` so distillation can later pattern-match
    player decision speed + counter-proposal frequency.
    """
    store = ApprovalRequests(db)
    requests = store.list_for_project(project_id)
    # Pull ``run_id`` from the DB row separately (the ApprovalRequest
    # model omits it for backward compat) so the episode payload can
    # group approvals by run alongside audit_events / health_events.
    rid_rows = db.execute(
        "SELECT id, run_id FROM approval_requests WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    rid_lookup = {r["id"]: r["run_id"] for r in rid_rows}

    events: list[ApprovalEvent] = []
    for request in requests:
        comments = store.list_comments(request.id)
        comment_models: list[ApprovalComment] = []
        for c in comments:
            # ``by`` is rendered as ``"<by_type>:<by_id>"`` when an id
            # is present (e.g. ``"agent:cfo"``) so distillation has a
            # single string key without re-joining two columns.
            by = (
                f"{c.by_type}:{c.by_id}"
                if c.by_id
                else c.by_type
            )
            created_str = (
                c.created_at.isoformat()
                if hasattr(c.created_at, "isoformat")
                else str(c.created_at)
            )
            comment_models.append(
                ApprovalComment(
                    by=by,
                    at=created_str,
                    text=c.body,
                )
            )
        decided_at = (
            request.resolved_at.isoformat()
            if request.resolved_at and hasattr(request.resolved_at, "isoformat")
            else (str(request.resolved_at) if request.resolved_at else None)
        )
        events.append(
            ApprovalEvent(
                id=request.id,
                run_id=rid_lookup.get(request.id),
                kind=request.action_type,
                outcome=request.status.value,
                comments=comment_models,
                decided_at=decided_at,
            )
        )
    return events


def collect_reflections(db: Database, project_id: str) -> list[ReflectionEntry]:
    rows = db.execute(
        """SELECT agent_role, category, content FROM agent_memories
           WHERE context = ? AND category = 'reflection'
           ORDER BY id""",
        (f"project:{project_id}",),
    ).fetchall()
    return [
        ReflectionEntry(
            agent_role=row["agent_role"],
            category=row["category"],
            content=row["content"],
        )
        for row in rows
    ]


def collect_glossary_drift(
    health_events: list[HealthEvent],
) -> list[GlossaryDriftEntry] | None:
    """Surface glossary drift hits this project recorded for distillation.

    Drift hits are written by the CoS retrospective scanner as part of
    the ``glossary_drift_alert`` health event's ``detail.drifts`` list.
    We aggregate every alert tied to the project into one flat list so
    distillation can scan the slot without re-walking ``health_events``.
    Returns ``None`` (not ``[]``) when no alerts exist so older payloads
    remain byte-equivalent on rebuild.
    """
    collected: list[GlossaryDriftEntry] = []
    for ev in health_events:
        if ev.kind != "glossary_drift_alert":
            continue
        drifts = ev.detail.get("drifts") if isinstance(ev.detail, dict) else None
        if not isinstance(drifts, list):
            continue
        for raw in drifts:
            if not isinstance(raw, dict):
                continue
            try:
                collected.append(GlossaryDriftEntry.model_validate(raw))
            except Exception:  # noqa: BLE001 — skip individual bad rows
                continue
    if not collected:
        return None
    return collected


def collect_targets_bundle(db: Database) -> TargetsBundleEntry | None:
    """Snapshot the three target states + the review approval thread id.

    Reads via :func:`kompany.state.targets.get_bundle`. Returns
    ``None`` only when no founder/proposal/agreed row and no review
    thread id exist — that keeps payloads materialised against
    legacy data unchanged.
    """
    try:
        bundle = _get_targets_bundle(db)
    except Exception:  # noqa: BLE001 — never let targets break materialize
        return None

    def _to_snapshot(model: Any) -> TargetsSnapshot | None:
        if model is None:
            return None
        return TargetsSnapshot(
            initial_budget=float(model.initial_budget),
            revenue_target=float(model.revenue_target),
            customer_target=model.customer_target,
            deadline=model.deadline,
            source=model.source,
        )

    founder_snap = _to_snapshot(bundle.founder)
    proposal_snap = _to_snapshot(bundle.proposal)
    agreed_snap = _to_snapshot(bundle.agreed)
    review_thread_id = bundle.review_thread_id

    # If absolutely nothing has been written, return None so older
    # payloads stay byte-equivalent.
    # ``get_bundle`` returns a default founder snapshot (all zeros)
    # even when no row exists, so we check for *any* non-default
    # signal: a non-zero number, a non-None deadline, a proposal /
    # agreed row, or a review thread id.
    meaningful_founder = founder_snap is not None and (
        founder_snap.initial_budget > 0
        or founder_snap.revenue_target > 0
        or founder_snap.customer_target is not None
        or founder_snap.deadline is not None
    )
    if (
        not meaningful_founder
        and proposal_snap is None
        and agreed_snap is None
        and review_thread_id is None
    ):
        return None

    return TargetsBundleEntry(
        founder=founder_snap if meaningful_founder else None,
        proposal=proposal_snap,
        agreed=agreed_snap,
        review_thread_id=review_thread_id,
    )
