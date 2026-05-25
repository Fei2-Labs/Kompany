"""Tests for the team-proposes-first-directives flow.

Defends the contract documented in ``docs/context/operations.md:60-62``
and the ``engineering-team-proposes-plan`` shared memory: when a
founder finishes onboarding with a template that ships zero
pre-staged directives, the engine must produce a team-generated
first-week directive triplet so the founder picks from it instead of
staring at an empty prompt.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from kompany.core.engine import KompanyEngine
from kompany.interfaces import api as api_module


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    return KompanyEngine()


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    api_module.reset_engine()
    return TestClient(api_module.app)


def _set_agreed_targets(engine, *, revenue=1000.0, customer=None,
                        deadline="2026-12-31T00:00:00+00:00"):
    engine.db.execute(
        """INSERT INTO company_config (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (
            "targets.agreed",
            json.dumps({
                "initial_budget": 100.0,
                "revenue_target": revenue,
                "customer_target": customer,
                "deadline": deadline,
                "source": "agreed",
            }),
        ),
    )
    engine.db.commit()


def test_propose_returns_empty_when_no_agreed_targets(engine):
    """Without agreed targets the engine refuses to fabricate a plan."""
    assert engine.propose_first_directives(skip_llm=True) == []


def test_propose_heuristic_writes_three_drafts(engine):
    _set_agreed_targets(engine, revenue=5000.0)

    items = engine.propose_first_directives()

    assert isinstance(items, list)
    assert len(items) == 3
    for item in items:
        assert {"id", "name", "type", "status", "rationale", "proposer_role"} <= set(item)
        assert item["status"] == "draft"
        assert item["name"]
        assert item["rationale"]
        assert item["proposer_role"] in {"ceo", "cro", "cpo", "cmo", "cfo"}


def test_propose_is_idempotent(engine):
    """Calling twice returns existing drafts — no second LLM spend."""
    _set_agreed_targets(engine)

    first = engine.propose_first_directives()
    second = engine.propose_first_directives()

    assert [r["id"] for r in second] == [r["id"] for r in first]


def test_propose_persists_status_draft_and_source_marker(engine):
    """Drafts must use the literal 'draft' status string the rest of
    the codebase + REST filter expect, with a plan source marker so
    template-staged drafts can be told apart from team-proposed."""
    _set_agreed_targets(engine)
    items = engine.propose_first_directives()

    placeholders = ",".join("?" * len(items))
    rows = engine.db.execute(
        f"SELECT id, status, plan FROM projects WHERE id IN ({placeholders})",
        [r["id"] for r in items],
    ).fetchall()
    assert {r["status"] for r in rows} == {"draft"}
    for r in rows:
        plan = json.loads(r["plan"])
        assert plan.get("source") == "team_proposal_first_week"


def test_existing_template_drafts_short_circuit(engine):
    """If template-staged drafts already exist, propose_first_directives
    returns them without spending another LLM call AND without writing
    new team_proposal_first_week rows."""
    _set_agreed_targets(engine)
    engine.apply_template("saas-startup")  # ships pre-staged drafts

    pre = engine.db.execute(
        "SELECT COUNT(*) AS n FROM projects WHERE status = 'draft'"
    ).fetchone()
    assert pre["n"] >= 1

    items = engine.propose_first_directives()
    sources = []
    for item in items:
        row = engine.db.execute(
            "SELECT plan FROM projects WHERE id = ?", (item["id"],)
        ).fetchone()
        plan = json.loads(row["plan"]) if row["plan"] else {}
        sources.append(plan.get("source") or "")
    assert "team_proposal_first_week" not in sources


# -------- REST -----------------------------------------------------------


def test_rest_endpoint_returns_directives(client):
    res = client.post("/onboarding/propose_first_directives")
    # No agreed targets in the fresh fixture install → empty list,
    # not an error. The frontend's fallback textarea covers this case.
    assert res.status_code == 200
    body = res.json()
    assert "directives" in body
    assert isinstance(body["directives"], list)
