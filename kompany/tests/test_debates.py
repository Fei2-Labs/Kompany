"""Tests for the debate persistence service."""

from __future__ import annotations

from kompany.core.debate_models import (
    AgentPosition,
    CEODecision,
    DebateRound,
    DebateSynthesis,
)
from kompany.core.run_context import run_scope
from kompany.state.database import Database
from kompany.state.debates import Debates


def _make_position(role: str = "ceo") -> AgentPosition:
    return AgentPosition(
        agent_role=role,
        agent_name=role.upper(),
        squad="strategy",
        round=DebateRound.POSITION,
        analysis="analysis text",
        recommendation="do X",
        confidence="high",
        concessions=["concede A"],
        hard_lines=["never B"],
    )


def _make_synthesis() -> DebateSynthesis:
    return DebateSynthesis(
        consensus_position="ship",
        key_tensions=["cost"],
        recommended_option="ship now",
        risk_flags=["risk"],
        decision_required="approve",
    )


def _make_decision() -> CEODecision:
    return CEODecision(
        decision="ship",
        rationale="market window",
        tradeoffs_weighed=["cost vs time"],
        overrides=[],
        next_steps=["execute"],
        confidence_score=0.8,
        reversibility="easily_reversible",
    )


def test_debate_record_and_get(tmp_path):
    debates = Debates(Database(tmp_path))
    debate_id = debates.record(
        rounds=[[_make_position("ceo"), _make_position("cfo")]],
        synthesis=_make_synthesis(),
        decision=_make_decision(),
        directive_id="dir-x",
        project_id="proj-1",
    )
    assert isinstance(debate_id, str) and debate_id

    row = debates.get(debate_id)
    assert row is not None
    assert row["directive_id"] == "dir-x"
    assert row["project_id"] == "proj-1"
    assert len(row["rounds"]) == 1
    assert len(row["rounds"][0]) == 2
    assert row["rounds"][0][0]["agent_role"] == "ceo"
    assert row["synthesis"]["consensus_position"] == "ship"
    assert row["decision"]["decision"] == "ship"


def test_debate_list_by_project(tmp_path):
    debates = Debates(Database(tmp_path))
    a = debates.record(
        rounds=[[_make_position("ceo")]],
        synthesis=None,
        decision=None,
        directive_id="dir-a",
        project_id="proj-1",
    )
    b = debates.record(
        rounds=[[_make_position("ceo")]],
        synthesis=None,
        decision=None,
        directive_id="dir-b",
        project_id="proj-1",
    )
    debates.record(
        rounds=[[_make_position("ceo")]],
        synthesis=None,
        decision=None,
        directive_id="dir-c",
        project_id="proj-2",
    )

    rows = debates.list_by_project("proj-1")
    ids = {r["id"] for r in rows}
    assert ids == {a, b}


def test_debate_get_missing_returns_none(tmp_path):
    debates = Debates(Database(tmp_path))
    assert debates.get("nope") is None


def test_debate_records_run_id_from_context(tmp_path):
    debates = Debates(Database(tmp_path))
    with run_scope() as rid:
        debate_id = debates.record(
            rounds=[[_make_position()]],
            synthesis=None,
            decision=None,
            directive_id="d",
        )
    row = debates.get(debate_id)
    assert row["run_id"] == rid


def test_debate_run_id_null_outside_scope(tmp_path):
    debates = Debates(Database(tmp_path))
    debate_id = debates.record(
        rounds=[[_make_position()]],
        synthesis=None,
        decision=None,
        directive_id="d",
    )
    row = debates.get(debate_id)
    assert row["run_id"] is None


def test_debate_handles_no_synthesis_no_decision(tmp_path):
    debates = Debates(Database(tmp_path))
    debate_id = debates.record(
        rounds=[[_make_position()]],
        synthesis=None,
        decision=None,
        directive_id="d",
    )
    row = debates.get(debate_id)
    assert row["synthesis"] is None
    assert row["decision"] is None
