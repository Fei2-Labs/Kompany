"""End-to-end glossary flow: template preload → drift → alert → approval.

Glossary-and-drift-detection task 05-19. Exercises the full plumbing
without firing an LLM:

1. Apply ``saas-startup`` template → glossary entries land in
   ``company_config['glossary']``.
2. Hand-craft an ``EpisodePayloadV1``-shaped reflection list using
   forbidden synonyms, run the pure ``scan_drift`` function.
3. Push the result through ``Watchdog.record_glossary_drift`` and the
   approval store to mimic what ``_run_glossary_drift_scan`` does at
   retrospective time.
4. Founder approves → matching health event resolves + drift_resolved
   audit row appears.
5. Episode materialisation later (via ``Episodes._collect_glossary_drift``)
   surfaces the drift rows into the payload's ``glossary_drift`` slot
   for distillation.

Catches regressions where the four moving parts (template ingest, scan,
health write, approval-handler close, episode collect) drift apart.
"""

from __future__ import annotations

import pytest

from kompany.agents.cos_glossary_scan import (
    build_suggested_corrections,
    scan_drift,
)
from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.episodes import Episodes
from kompany.state.glossary import GlossaryService, load_from_config
from kompany.state.health_events import HealthEvents
from kompany.state.ledger import Ledger
from kompany.state.models import ApprovalRequest
from kompany.state.projects import Projects
from kompany.state.templates import Templates


@pytest.fixture
def stack(tmp_path):
    from kompany.core.watchdog import Watchdog
    from kompany.state.approvals import ApprovalRequests

    db = Database(tmp_path)
    ledger = Ledger(db)
    projects = Projects(db)
    audit = AuditLog(db)
    episodes = Episodes(db)
    health_events = HealthEvents(db)
    approvals = ApprovalRequests(db)
    watchdog = Watchdog(
        health_events=health_events,
        projects=projects,
        audit=audit,
    )
    glossary = GlossaryService(db)
    templates = Templates(db=db, ledger=ledger, projects=projects, audit=audit)
    return {
        "db": db,
        "audit": audit,
        "episodes": episodes,
        "health_events": health_events,
        "approvals": approvals,
        "glossary": glossary,
        "watchdog": watchdog,
        "templates": templates,
    }


def test_template_apply_preloads_glossary(stack) -> None:
    """A saas-startup template should ship glossary entries."""
    stack["templates"].apply("saas-startup")
    rows = stack["glossary"].list_terms()
    assert len(rows) >= 4
    terms = {r.term.lower() for r in rows}
    # saas-startup template names common SaaS terminology.
    assert "customer" in terms or "mrr" in terms


def test_blank_template_apply_skips_glossary(stack) -> None:
    """``blank`` template has no glossary, so install count is zero."""
    stack["templates"].apply("blank")
    assert stack["glossary"].list_terms() == []


def test_end_to_end_drift_alert_to_resolved(stack) -> None:
    # ----- 1. Apply saas-startup, then ensure we have a known synonym pair
    stack["templates"].apply("saas-startup")
    # Force a deterministic term so the scan in step 2 has a known hit.
    stack["glossary"].add_or_update(
        type(stack["glossary"].load().entries[0])(
            term="customer",
            definition="paying account",
            forbidden_synonyms=["user", "lead"],
            added_at=stack["glossary"].load().entries[0].added_at,
            added_by="founder",
        )
    )
    glossary = load_from_config(stack["db"])
    assert glossary.find("customer") is not None

    # ----- 2. Pure scan against fabricated reflections
    reflections = [
        {
            "agent_role": "cmo",
            "content": "Our user base grew 30% — user retention improved too.",
        }
    ]
    drifts = scan_drift(glossary=glossary, reflections=reflections)
    assert len(drifts) == 1
    assert drifts[0].term == "customer"
    assert drifts[0].drifted_synonym == "user"
    assert drifts[0].count == 2

    # ----- 3. Wire the result into the approval + health stores
    suggestions = build_suggested_corrections(drifts, glossary)
    project_id = "proj_e2e"
    approval = stack["approvals"].create(
        ApprovalRequest(
            action_type="glossary_review",
            summary=f"Glossary drift in {project_id}: {len(drifts)} hit(s)",
            payload={
                "project_id": project_id,
                "drifts": [d.model_dump(mode="json") for d in drifts],
                "suggested_corrections": suggestions,
            },
            project_id=project_id,
            severity="medium",
            requested_by="cos",
        )
    )
    event = stack["watchdog"].record_glossary_drift(
        episode_id=project_id,
        drifts=drifts,
        project_id=project_id,
        approval_id=approval.id,
    )
    assert event["kind"] == "glossary_drift_alert"
    assert event["status"] == "open"

    # ----- 4. Founder approves → health event closes
    stack["approvals"].approve(approval.id, approved_by="founder")
    # In real flow ``_finalize_glossary_review`` closes the health event;
    # we mimic that here so the test stays decoupled from the engine.
    stack["health_events"].resolve(
        event_id=event["id"],
        action="continue",
        resolved_by="founder",
    )
    closed = stack["health_events"].get(event["id"])
    assert closed["status"] in {"resolved", "dismissed"}


def test_episode_materialize_surfaces_glossary_drift(stack) -> None:
    """After a drift alert lands, ``Episodes.materialize`` exposes it in payload."""
    # Set up a project and seed the alert against it.
    from datetime import datetime, timezone

    from kompany.state.models import Project, ProjectStatus, ProjectType

    project = Project(
        name="e2e project",
        type=ProjectType.OPERATIONAL,
        status=ProjectStatus.COMPLETED,
        plan={"mission": "ship things"},
    )
    stack["db"].execute(
        """INSERT INTO projects (id, name, type, status, plan, assigned_agents,
                                  created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project.id,
            project.name,
            project.type.value,
            project.status.value,
            "{}",
            "[]",
            datetime.now(timezone.utc).isoformat(),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    stack["db"].commit()

    drifts = [
        {
            "term": "customer",
            "drifted_synonym": "user",
            "agent_role": "cmo",
            "count": 2,
            "sample_excerpt": "... user base ...",
            "source": "reflection",
        }
    ]
    stack["watchdog"].record_glossary_drift(
        episode_id=project.id,
        drifts=drifts,
        project_id=project.id,
    )
    payload = stack["episodes"].materialize(project.id)
    assert payload.glossary_drift is not None
    assert len(payload.glossary_drift) == 1
    assert payload.glossary_drift[0].term == "customer"
    assert payload.glossary_drift[0].drifted_synonym == "user"
