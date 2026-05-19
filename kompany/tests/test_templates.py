"""Tests for the company-template service.

Covers:

* Pydantic schema validation (all 6 shipped manifests parse, unknown
  agent role rejected, ``extra="forbid"`` catches typos).
* ``list_templates`` + ``show`` filesystem discovery.
* ``apply`` writes config, ledger, draft projects, audit event.
* ``apply`` idempotency: second call raises without ``force`` and
  overwrites with it.
* ``override_budget`` / ``override_directive`` plumbing.
* Packaged template files are reachable via ``importlib.resources``
  (so the wheel-bundling story works).
"""

from __future__ import annotations

import importlib.resources
import json

import pytest
from pydantic import ValidationError

from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.projects import Projects
from kompany.state.templates import (
    TemplateAlreadyApplied,
    TemplateNotFound,
    Templates,
)
from kompany.state.templates_model import CompanyTemplate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service(tmp_path):
    db = Database(tmp_path)
    ledger = Ledger(db)
    projects = Projects(db)
    audit = AuditLog(db)
    return Templates(db=db, ledger=ledger, projects=projects, audit=audit), db


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------


def test_company_template_minimal_round_trip():
    tpl = CompanyTemplate(
        id="t1",
        name="Test",
        mission_title="Mission",
        mission_md_path="mission.md",
        initial_budget=100.0,
    )
    assert tpl.id == "t1"
    assert tpl.enabled_agents == []
    assert tpl.suggested_directives == []
    assert tpl.rpg_theme == ""


def test_company_template_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        CompanyTemplate(
            id="t1",
            name="Test",
            mission_title="m",
            mission_md_path="mission.md",
            initial_budget=0,
            typo_field="oops",  # type: ignore[call-arg]
        )


def test_company_template_rejects_unknown_agent_role():
    with pytest.raises(ValidationError) as exc:
        CompanyTemplate(
            id="t1",
            name="Test",
            mission_title="m",
            mission_md_path="mission.md",
            initial_budget=0,
            enabled_agents=["ceo", "not_a_real_role"],
        )
    assert "not_a_real_role" in str(exc.value)


def test_company_template_dedupes_enabled_agents():
    tpl = CompanyTemplate(
        id="t1",
        name="Test",
        mission_title="m",
        mission_md_path="mission.md",
        initial_budget=0,
        enabled_agents=["ceo", "cfo", "ceo"],
    )
    assert tpl.enabled_agents == ["ceo", "cfo"]


def test_company_template_negative_budget_rejected():
    with pytest.raises(ValidationError):
        CompanyTemplate(
            id="t1",
            name="Test",
            mission_title="m",
            mission_md_path="mission.md",
            initial_budget=-1.0,
        )


# ---------------------------------------------------------------------------
# Shipped manifests parse and are discoverable
# ---------------------------------------------------------------------------


SHIPPED_TEMPLATE_IDS = {
    "saas-startup",
    "indie-tool",
    "consulting-firm",
    "content-creator",
    "ecommerce",
    "blank",
}


def test_all_shipped_manifests_parse(service):
    svc, _ = service
    rows = svc.list_templates()
    ids = {row.id for row in rows}
    assert SHIPPED_TEMPLATE_IDS.issubset(ids), (
        f"Missing shipped templates: {SHIPPED_TEMPLATE_IDS - ids}"
    )


def test_list_templates_returns_pydantic_models(service):
    svc, _ = service
    rows = svc.list_templates()
    for row in rows:
        assert isinstance(row, CompanyTemplate)
        assert row.id
        assert row.mission_title


def test_show_returns_pydantic_model(service):
    svc, _ = service
    tpl = svc.show("saas-startup")
    assert tpl.id == "saas-startup"
    assert tpl.initial_budget == 5000.0
    assert "ceo" in tpl.enabled_agents


def test_show_with_mission_reads_md_file(service):
    svc, _ = service
    tpl, mission = svc.show_with_mission("saas-startup")
    assert tpl.id == "saas-startup"
    # Mission body preserves markdown verbatim (don't sanitize)
    assert mission.startswith("# Mission")
    assert "$1M ARR" in mission


def test_show_unknown_template_raises(service):
    svc, _ = service
    with pytest.raises(TemplateNotFound):
        svc.show("does-not-exist")


def test_blank_template_has_no_directives(service):
    svc, _ = service
    tpl = svc.show("blank")
    assert tpl.suggested_directives == []
    assert tpl.initial_budget == 1000.0
    assert len(tpl.enabled_agents) == 11


# ---------------------------------------------------------------------------
# Apply happy path
# ---------------------------------------------------------------------------


def test_apply_saas_startup_writes_config(service):
    svc, db = service
    result = svc.apply("saas-startup")

    cfg = {
        row["key"]: row["value"]
        for row in db.execute("SELECT key, value FROM company_config").fetchall()
    }
    assert cfg["template_id"] == "saas-startup"
    assert "$1M ARR" in cfg["mission"]
    assert cfg["mission_title"] == "Build a B2B SaaS to $1M ARR"
    assert float(cfg["initial_budget"]) == 5000.0
    assert json.loads(cfg["enabled_agents"]) == [
        "ceo", "cfo", "cto", "cpo", "cmo", "cro",
        "coo", "csa", "ciso", "cos", "cv",
    ]
    assert result.template_id == "saas-startup"
    assert result.initial_budget == 5000.0


def test_apply_writes_initial_budget_to_ledger(service):
    svc, db = service
    svc.apply("saas-startup")
    balance = Ledger(db).get_balance()
    assert balance == 5000.0


def test_apply_creates_draft_projects(service):
    svc, db = service
    result = svc.apply("saas-startup")
    rows = db.execute(
        "SELECT id, name, status, plan FROM projects WHERE status = 'draft'"
    ).fetchall()
    assert len(rows) == 3  # saas-startup has 3 suggested directives
    assert set(result.project_ids) == {row["id"] for row in rows}
    # Each project's plan includes the mission verbatim
    plan = json.loads(rows[0]["plan"])
    assert "mission" in plan
    assert "$1M ARR" in plan["mission"]
    assert "suggested_directive" in plan
    assert plan["template_id"] == "saas-startup"


def test_apply_writes_audit_event(service):
    svc, db = service
    svc.apply("saas-startup")
    rows = db.execute(
        "SELECT event_type, detail FROM audit_log "
        "WHERE event_type = 'company.template_applied'"
    ).fetchall()
    assert len(rows) == 1
    detail = json.loads(rows[0]["detail"])
    assert detail["template_id"] == "saas-startup"
    assert detail["initial_budget"] == 5000.0
    assert len(detail["project_ids"]) == 3


def test_apply_blank_template_creates_no_projects(service):
    svc, db = service
    result = svc.apply("blank")
    assert result.project_ids == []
    rows = db.execute("SELECT id FROM projects WHERE status = 'draft'").fetchall()
    assert rows == []


# ---------------------------------------------------------------------------
# Idempotency / force
# ---------------------------------------------------------------------------


def test_reapply_without_force_raises(service):
    svc, _ = service
    svc.apply("saas-startup")
    with pytest.raises(TemplateAlreadyApplied):
        svc.apply("saas-startup")


def test_reapply_with_force_overwrites(service):
    svc, db = service
    svc.apply("saas-startup")
    # Different template + force=True
    result = svc.apply("indie-tool", force=True)
    assert result.template_id == "indie-tool"
    assert result.force is True
    cfg_row = db.execute(
        "SELECT value FROM company_config WHERE key = 'template_id'"
    ).fetchone()
    assert cfg_row["value"] == "indie-tool"
    # Both ledger rows present (one per apply call)
    ledger_rows = db.execute("SELECT amount FROM ledger ORDER BY id").fetchall()
    assert [r["amount"] for r in ledger_rows] == [5000.0, 1000.0]


def test_is_applied_returns_current_template_id(service):
    svc, _ = service
    assert svc.is_applied() is None
    svc.apply("indie-tool")
    assert svc.is_applied() == "indie-tool"


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------


def test_override_budget(service):
    svc, db = service
    svc.apply("saas-startup", override_budget=2000.0)
    balance = Ledger(db).get_balance()
    assert balance == 2000.0
    cfg = db.execute(
        "SELECT value FROM company_config WHERE key = 'initial_budget'"
    ).fetchone()
    assert float(cfg["value"]) == 2000.0


def test_override_directive_replaces_suggested_directives(service):
    svc, db = service
    custom = "Build a paperclip empire to dominate the universe"
    result = svc.apply("saas-startup", override_directive=custom)
    assert len(result.project_ids) == 1
    row = db.execute(
        "SELECT name, plan FROM projects WHERE id = ?",
        (result.project_ids[0],),
    ).fetchone()
    plan = json.loads(row["plan"])
    assert plan["suggested_directive"] == custom


def test_apply_unknown_template_raises(service):
    svc, _ = service
    with pytest.raises(TemplateNotFound):
        svc.apply("not-a-template")


# ---------------------------------------------------------------------------
# Packaging — make sure the wheel ships the data
# ---------------------------------------------------------------------------


def test_packaged_manifest_is_reachable_via_importlib_resources():
    """The exact check from PRD #8 — ensures ``package_data`` is right."""
    raw = (
        importlib.resources.files("kompany")
        .joinpath("templates/saas-startup/manifest.json")
        .read_text(encoding="utf-8")
    )
    data = json.loads(raw)
    assert data["id"] == "saas-startup"


def test_packaged_mission_md_is_reachable():
    body = (
        importlib.resources.files("kompany")
        .joinpath("templates/blank/mission.md")
        .read_text(encoding="utf-8")
    )
    assert "Blank Slate" in body
