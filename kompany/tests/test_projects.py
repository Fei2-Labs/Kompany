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
