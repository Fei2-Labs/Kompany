"""End-to-end: fresh DB → apply a template → episode materialize sees mission.

This covers PRD acceptance criterion #7 ("episode materialize after apply
includes project_meta.mission correctly populated") and the broader claim
that the four-surface engine entry point (``apply_template``) wires the
state services together without the caller having to know about
``Templates`` directly.
"""

from __future__ import annotations

import json

import pytest

from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.episodes import Episodes
from kompany.state.ledger import Ledger
from kompany.state.projects import Projects
from kompany.state.templates import Templates


@pytest.fixture
def stack(tmp_path):
    db = Database(tmp_path)
    ledger = Ledger(db)
    projects = Projects(db)
    audit = AuditLog(db)
    episodes = Episodes(db)
    templates = Templates(db=db, ledger=ledger, projects=projects, audit=audit)
    return {
        "db": db,
        "ledger": ledger,
        "projects": projects,
        "audit": audit,
        "episodes": episodes,
        "templates": templates,
    }


def test_apply_then_episode_materialize_carries_mission(stack):
    """PRD test case #7: after apply, episode payload's project_meta has the
    mission populated from the template, not None."""
    templates: Templates = stack["templates"]
    episodes: Episodes = stack["episodes"]

    result = templates.apply("saas-startup")
    assert result.project_ids, "expected at least one draft project"

    project_id = result.project_ids[0]
    payload = episodes.materialize(project_id)

    assert payload.project_meta.mission is not None
    assert "$1M ARR" in payload.project_meta.mission


def test_apply_then_balance_matches_initial_budget(stack):
    templates: Templates = stack["templates"]
    ledger: Ledger = stack["ledger"]

    templates.apply("indie-tool")
    assert ledger.get_balance() == 1000.0


def test_apply_audit_event_includes_run_context(stack):
    templates: Templates = stack["templates"]
    db = stack["db"]

    templates.apply("consulting-firm")
    rows = db.execute(
        "SELECT detail, run_id FROM audit_log "
        "WHERE event_type = 'company.template_applied'"
    ).fetchall()
    assert len(rows) == 1
    detail = json.loads(rows[0]["detail"])
    assert detail["template_id"] == "consulting-firm"
    # 3 suggested directives for consulting-firm
    assert len(detail["project_ids"]) == 3


def test_fresh_db_to_materialize_for_all_starter_templates(stack):
    """Smoke test: every shipped template can be applied to a fresh DB and
    its first draft project can be materialized into an EpisodePayloadV1
    (proving the cross-component plumbing works for each scenario, not
    just the saas-startup happy path)."""
    templates: Templates = stack["templates"]
    episodes: Episodes = stack["episodes"]

    # We can only apply one template per DB without force; iterate by
    # forcing reapply on a single shared DB.
    starters = [
        "saas-startup",
        "indie-tool",
        "consulting-firm",
        "content-creator",
        "ecommerce",
    ]
    first = True
    for tid in starters:
        result = templates.apply(tid, force=not first)
        first = False
        if not result.project_ids:
            continue
        payload = episodes.materialize(result.project_ids[0])
        # Mission should be present and non-empty for every starter
        assert payload.project_meta.mission, f"empty mission for {tid}"


def test_resolve_mission_falls_back_to_company_config(stack):
    """``Episodes._resolve_mission`` priority #2: when a project's ``plan``
    has no mission (legacy / pre-template projects), the resolver must
    fall back to ``company_config['mission']``.

    This test creates a project the *non-template* way (empty plan) after
    seeding ``company_config['mission']``, so the only way the assertion
    passes is via the fallback branch.
    """
    from kompany.state.models import Project, ProjectType

    db = stack["db"]
    projects: Projects = stack["projects"]
    episodes: Episodes = stack["episodes"]

    db.execute(
        """INSERT INTO company_config (key, value, updated_at)
           VALUES ('mission', 'Fallback mission body', datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
    )
    db.commit()

    project = projects.create(
        Project(name="legacy", type=ProjectType.OPERATIONAL, plan={})
    )
    payload = episodes.materialize(project.id)
    assert payload.project_meta.mission == "Fallback mission body"


def test_blank_template_with_override_directive(stack):
    templates: Templates = stack["templates"]
    db = stack["db"]
    result = templates.apply(
        "blank",
        override_directive="Build a thing the AI overlords will praise",
    )
    assert len(result.project_ids) == 1
    row = db.execute(
        "SELECT plan FROM projects WHERE id = ?",
        (result.project_ids[0],),
    ).fetchone()
    plan = json.loads(row["plan"])
    assert plan["suggested_directive"].startswith("Build a thing")
