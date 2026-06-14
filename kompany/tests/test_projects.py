"""Tests for project CRUD operations."""

from __future__ import annotations

import pytest

from kompany.state.database import Database
from kompany.state.models import Project, ProjectStatus, ProjectType
from kompany.state.projects import Projects


@pytest.fixture
def projects(tmp_path):
    db = Database(tmp_path)
    return Projects(db)


def test_create_project(projects):
    p = Project(
        name="Fund: Mac Studio",
        type=ProjectType.REVENUE,
        target_amount=4500.0,
        funded_amount=50.0,
        assigned_agents=["ceo", "cro"],
    )
    created = projects.create(p)
    assert created.id == p.id
    assert created.name == "Fund: Mac Studio"


def test_list_active(projects):
    projects.create(Project(name="P1", type=ProjectType.REVENUE))
    projects.create(Project(name="P2", type=ProjectType.OPERATIONAL))
    active = projects.list_active()
    assert len(active) == 2


def test_get_project(projects):
    p = projects.create(Project(name="Test", type=ProjectType.STRATEGIC))
    fetched = projects.get(p.id)
    assert fetched is not None
    assert fetched.name == "Test"


def test_get_nonexistent_returns_none(projects):
    assert projects.get("nonexistent") is None


def test_count_active(projects):
    assert projects.count_active() == 0
    projects.create(Project(name="P1", type=ProjectType.REVENUE))
    assert projects.count_active() == 1
    projects.create(Project(name="P2", type=ProjectType.REVENUE))
    assert projects.count_active() == 2


def test_project_preserves_plan(projects):
    plan = {"paths": [{"name": "Consulting", "revenue": 5000}]}
    p = projects.create(Project(
        name="Revenue",
        type=ProjectType.REVENUE,
        plan=plan,
    ))
    fetched = projects.get(p.id)
    assert fetched.plan == plan


def test_project_preserves_agents(projects):
    p = projects.create(Project(
        name="Revenue",
        type=ProjectType.REVENUE,
        assigned_agents=["ceo", "cro", "cmo"],
    ))
    fetched = projects.get(p.id)
    assert fetched.assigned_agents == ["ceo", "cro", "cmo"]


# --- Defensive read-path hardening (handoff 2026-06-15: restructure +
# ADR-0005/0006/0007 narrowed the schema; old DB rows must not crash reads) ---

def test_legacy_type_coerced_on_read(projects):
    """Legacy ``type`` values (growth/dev) coerce onto the current enum
    instead of raising ValidationError."""
    p = Project(name="legacy", type=ProjectType.REVENUE, target_amount=1.0)
    projects.create(p)
    # Simulate a pre-enum-narrowing row.
    projects.db.execute("UPDATE projects SET type = 'growth' WHERE id = ?", (p.id,))
    projects.db.commit()
    got = projects.get(p.id)
    assert got is not None
    assert got.type == ProjectType.STRATEGIC  # growth -> strategic
    projects.db.execute("UPDATE projects SET type = 'dev' WHERE id = ?", (p.id,))
    projects.db.commit()
    assert projects.get(p.id).type == ProjectType.OPERATIONAL  # dev -> operational


def test_unknown_type_falls_back_not_crashes(projects):
    p = Project(name="weird", type=ProjectType.REVENUE, target_amount=1.0)
    projects.create(p)
    projects.db.execute("UPDATE projects SET type = 'zzz' WHERE id = ?", (p.id,))
    projects.db.commit()
    assert projects.get(p.id).type == ProjectType.OPERATIONAL


def test_malformed_plan_json_does_not_crash_list(projects):
    """One row with a corrupt ``plan`` must not crash list_active()."""
    p = Project(name="bad-plan", type=ProjectType.REVENUE, target_amount=1.0,
                status=ProjectStatus.ACTIVE)
    projects.create(p)
    # Valid JSON + appended garbage = "Extra data" (the real xg671610 case).
    projects.db.execute(
        "UPDATE projects SET plan = ? WHERE id = ?",
        ('{"ok": 1} trailing text', p.id),
    )
    projects.db.commit()
    active = projects.list_active()
    assert any(x.id == p.id for x in active)
    assert next(x for x in active if x.id == p.id).plan == {}


def test_malformed_assigned_agents_falls_back_to_empty(projects):
    p = Project(name="bad-agents", type=ProjectType.REVENUE, target_amount=1.0)
    projects.create(p)
    projects.db.execute(
        "UPDATE projects SET assigned_agents = ? WHERE id = ?", ("not json", p.id)
    )
    projects.db.commit()
    assert projects.get(p.id).assigned_agents == []
