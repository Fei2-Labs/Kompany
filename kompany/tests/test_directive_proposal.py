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


def test_propose_no_targets(engine):
    """Without agreed targets the engine refuses to fabricate a plan,
    surfacing the structured no_targets status to the UI."""
    result = engine.propose_first_directives(skip_llm=True)
    assert result["status"] == "no_targets"
    assert result["directives"] == []
    assert result["error_code"] == "no_targets"


def test_propose_heuristic_writes_three_drafts(engine):
    """KOMPANY_TEST_MODE skips the LLM and returns heuristic seeds
    with an explicit `status='heuristic'` so the UI can label them."""
    _set_agreed_targets(engine, revenue=5000.0)

    result = engine.propose_first_directives()

    assert result["status"] == "heuristic"
    items = result["directives"]
    assert isinstance(items, list)
    assert len(items) == 3
    for item in items:
        assert {"id", "name", "type", "status", "rationale", "proposer_role"} <= set(item)
        assert item["status"] == "draft"
        assert item["name"]
        assert item["rationale"]
        assert item["proposer_role"] in {"ceo", "cro", "cpo", "cmo", "cfo"}


def test_propose_is_idempotent(engine):
    """Calling twice returns existing drafts — no second LLM spend.
    Idempotent calls report status='ok' regardless of how the original
    drafts got created."""
    _set_agreed_targets(engine)

    first = engine.propose_first_directives()
    second = engine.propose_first_directives()

    assert second["status"] == "ok"
    assert [r["id"] for r in second["directives"]] == [r["id"] for r in first["directives"]]


def test_propose_heuristic_uses_distinct_source_marker(engine):
    """Heuristic seeds get source='team_proposal_first_week_heuristic'
    so downstream filtering can tell them apart from real team
    proposals — important for distillation (don't learn from generic
    seeds) and for the timeline ('team thought' vs 'fallback used')."""
    _set_agreed_targets(engine)
    result = engine.propose_first_directives()

    placeholders = ",".join("?" * len(result["directives"]))
    rows = engine.db.execute(
        f"SELECT id, status, plan FROM projects WHERE id IN ({placeholders})",
        [r["id"] for r in result["directives"]],
    ).fetchall()
    assert {r["status"] for r in rows} == {"draft"}
    for r in rows:
        plan = json.loads(r["plan"])
        # Heuristic path under KOMPANY_TEST_MODE
        assert plan.get("source") == "team_proposal_first_week_heuristic"


def test_existing_template_drafts_short_circuit(engine):
    """If template-staged drafts already exist, propose_first_directives
    returns them with status='ok' without spending another LLM call
    AND without writing new team_proposal_first_week rows."""
    _set_agreed_targets(engine)
    engine.apply_template("saas-startup")  # ships pre-staged drafts

    pre = engine.db.execute(
        "SELECT COUNT(*) AS n FROM projects WHERE status = 'draft'"
    ).fetchone()
    assert pre["n"] >= 1

    result = engine.propose_first_directives()
    assert result["status"] == "ok"
    sources = []
    for item in result["directives"]:
        row = engine.db.execute(
            "SELECT plan FROM projects WHERE id = ?", (item["id"],)
        ).fetchone()
        plan = json.loads(row["plan"]) if row["plan"] else {}
        sources.append(plan.get("source") or "")
    assert "team_proposal_first_week" not in sources
    assert "team_proposal_first_week_heuristic" not in sources


def test_propose_team_failed_surfaces_classified_error(engine, monkeypatch):
    """When the LLM call raises, the result must carry status='team_failed'
    plus a classified error_code so the UI can render the right
    quota/auth/network/provider_error guidance."""
    _set_agreed_targets(engine)
    monkeypatch.delenv("KOMPANY_TEST_MODE", raising=False)

    def boom(_self, _agreed):
        raise RuntimeError("Error code: 429 - rate limited, you exceeded your quota")

    monkeypatch.setattr(
        "kompany.core.directive_proposal.DirectiveProposalMixin._llm_first_directives",
        boom,
    )

    result = engine.propose_first_directives()
    assert result["status"] == "team_failed"
    assert result["error_code"] == "rate_limited"
    assert "quota" in result["error_message"].lower()
    assert result["directives"] == []


# -------- REST -----------------------------------------------------------


def test_rest_endpoint_returns_structured_result(client):
    res = client.post("/onboarding/propose_first_directives")
    assert res.status_code == 200
    body = res.json()
    # New shape — UI distinguishes ok / team_failed / heuristic / no_targets.
    assert {"status", "directives", "error_code", "error_message", "provider"} <= set(body)
    assert isinstance(body["directives"], list)


def test_heuristic_carries_rich_fields(engine):
    """Heuristic directives must populate week_plan + success_metric +
    expected_cost_usd + other_agents_involved so the UI cards aren't
    abstract."""
    _set_agreed_targets(engine)
    items = engine.propose_first_directives()["directives"]
    for d in items:
        assert isinstance(d.get("week_plan"), list) and len(d["week_plan"]) >= 3
        assert d.get("success_metric")
        assert isinstance(d.get("expected_cost_usd"), float)
        assert isinstance(d.get("other_agents_involved"), list)


def test_discuss_rejects_empty_question(engine):
    _set_agreed_targets(engine)
    engine.propose_first_directives()
    result = engine.discuss_first_directives("   ")
    assert result["status"] == "team_failed"
    assert result["error_code"] == "empty_question"


def test_discuss_requires_existing_directives(engine):
    _set_agreed_targets(engine)
    # No proposal yet → discussion is meaningless.
    result = engine.discuss_first_directives("which one should I pick?")
    assert result["status"] == "no_directives"


def test_discuss_no_targets(engine):
    result = engine.discuss_first_directives("hello")
    assert result["status"] == "no_targets"


def test_discuss_team_failed_classifies_error(engine, monkeypatch):
    _set_agreed_targets(engine)
    engine.propose_first_directives()  # seed drafts

    class _BoomAgent:
        def call_structured(self, **_kw):
            raise RuntimeError("Error code: 401 - invalid api key")

    monkeypatch.setattr(engine.registry, "get", lambda *a, **kw: _BoomAgent())
    result = engine.discuss_first_directives("why option 2?")
    assert result["status"] == "team_failed"
    assert result["error_code"] == "unauthorized"


def test_rest_endpoint_force_heuristic_opt_in(client):
    """When the founder clicks "use starter pack" on the error screen,
    the frontend POSTs {force_heuristic: true} so the heuristic path
    is explicit, not silent."""
    # Fresh install has no agreed targets so still no_targets result,
    # but force_heuristic should still be accepted as a body field.
    res = client.post(
        "/onboarding/propose_first_directives",
        json={"force_heuristic": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert "status" in body
