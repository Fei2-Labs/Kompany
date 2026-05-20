"""Tests for the onboard-v2 First Move activate flow.

PRD: ``.trellis/tasks/05-19-onboard-v2-flow/prd.md``. After
``apply_template`` stages each ``suggested_directive`` as a
``status='draft'`` project, the wizard's First Move step lets the
founder pick one and POST ``/projects/{id}/activate`` flips it to
``active``. The two unselected drafts stay in ``draft`` until later.

We invoke the FastAPI handlers directly (no ``TestClient``) so the
SQLite connection stays on a single thread — the engine's DB wrapper
keeps its connection sticky, and ``TestClient`` would dispatch the
handler onto an anyio worker thread that can't see it.

Test surface:

1. ``list_projects(include_draft=True)`` returns the staged drafts.
2. ``list_projects()`` (default) hides them.
3. ``activate_project`` flips one draft to active and audit-logs.
4. ``activate_project`` is idempotent on an already-active project.
5. ``activate_project`` raises 404 on an unknown id.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    # Make sure each test starts with a fresh engine cache.
    from kompany.interfaces import api as api_module

    api_module.reset_engine()


@pytest.fixture
def applied_engine() -> tuple[object, list[str]]:
    """Apply the saas-startup template (3 draft directives) so the
    projects table has rows the activate endpoint can flip."""
    from kompany.interfaces.api import get_engine

    engine = get_engine()
    result = engine.apply_template("saas-startup")
    project_ids = list(result.get("project_ids") or [])
    assert len(project_ids) >= 1, "template should stage at least one draft project"
    return engine, project_ids


# ---------------------------------------------------------------------------
# 1. /projects?include_draft=1
# ---------------------------------------------------------------------------


def test_list_projects_excludes_drafts_by_default(applied_engine: tuple[object, list[str]]) -> None:
    from kompany.interfaces.api import list_projects

    rows = list_projects(include_draft=False)
    statuses = {r["status"] for r in rows}
    assert "draft" not in statuses


def test_list_projects_include_draft_returns_drafts(applied_engine: tuple[object, list[str]]) -> None:
    from kompany.interfaces.api import list_projects

    _, project_ids = applied_engine
    rows = list_projects(include_draft=True)
    draft_ids = {r["id"] for r in rows if r["status"] == "draft"}
    assert draft_ids.issuperset(set(project_ids))


# ---------------------------------------------------------------------------
# 2. activate flips one draft, leaves others alone
# ---------------------------------------------------------------------------


def test_activate_flips_draft_to_active(applied_engine: tuple[object, list[str]]) -> None:
    from kompany.interfaces.api import activate_project, list_projects

    _, project_ids = applied_engine
    target = project_ids[0]
    payload = activate_project(target)
    assert payload["id"] == target
    assert payload["status"] == "active"
    assert payload["previous_status"] == "draft"

    rows = list_projects(include_draft=True)
    by_id = {r["id"]: r for r in rows}
    assert by_id[target]["status"] == "active"
    for other in [pid for pid in project_ids if pid != target]:
        assert by_id[other]["status"] == "draft"


def test_activate_audit_records_transition(applied_engine: tuple[object, list[str]]) -> None:
    from kompany.interfaces.api import activate_project

    engine, project_ids = applied_engine
    target = project_ids[0]
    activate_project(target)
    rows = engine.db.execute(
        "SELECT event_type, project_id FROM audit_log "
        "WHERE event_type = 'project.activated' AND project_id = ?",
        (target,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["project_id"] == target


# ---------------------------------------------------------------------------
# 3. Idempotent on already-active
# ---------------------------------------------------------------------------


def test_activate_on_already_active_is_idempotent(applied_engine: tuple[object, list[str]]) -> None:
    from kompany.interfaces.api import activate_project

    _, project_ids = applied_engine
    target = project_ids[0]
    activate_project(target)
    payload = activate_project(target)
    assert payload["status"] == "active"
    assert payload["previous_status"] == "active"


# ---------------------------------------------------------------------------
# 4. 404 on unknown id
# ---------------------------------------------------------------------------


def test_activate_unknown_project_raises_404() -> None:
    from kompany.interfaces.api import activate_project

    with pytest.raises(HTTPException) as exc_info:
        activate_project("proj_does_not_exist")
    assert exc_info.value.status_code == 404
    assert "not found" in str(exc_info.value.detail).lower()
