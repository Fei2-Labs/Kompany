"""Tests for the CEO-channel conversation store (sessions + turns)."""

from __future__ import annotations

import pytest

from kompany.core.run_context import is_valid_run_id, run_scope
from kompany.state.conversation import (
    MAX_CLARIFY_TURNS,
    ConversationStore,
    IllegalSessionTransition,
)
from kompany.state.database import Database
from kompany.state.models import SessionStatus


def _store(tmp_path) -> ConversationStore:
    return ConversationStore(Database(tmp_path))


def test_create_and_get_session(tmp_path):
    store = _store(tmp_path)
    session = store.create_session()

    fetched = store.get_session(session.id)
    assert fetched is not None
    assert fetched.id == session.id
    assert fetched.state == SessionStatus.OPEN
    assert fetched.clarify_turns == 0
    assert fetched.created_at is not None


def test_add_turn_orders_and_increments_index(tmp_path):
    store = _store(tmp_path)
    session = store.create_session()

    store.add_turn(session.id, role="founder", content="hi", kind="message")
    store.add_turn(session.id, role="ceo", content="hello", kind="final")

    turns = store.session_turns(session.id)
    assert [t.turn_index for t in turns] == [0, 1]
    assert [t.role for t in turns] == ["founder", "ceo"]
    assert [t.content for t in turns] == ["hi", "hello"]


def test_clarify_question_turn_bumps_session_counter(tmp_path):
    store = _store(tmp_path)
    session = store.create_session()

    store.add_turn(session.id, role="founder", content="do stuff", kind="message")
    store.add_turn(
        session.id, role="ceo", content="which stuff?", kind="clarify_question"
    )

    refreshed = store.get_session(session.id)
    assert refreshed.clarify_turns == 1
    # A plain message turn does NOT bump the counter.
    store.add_turn(session.id, role="founder", content="the blue stuff", kind="message")
    assert store.get_session(session.id).clarify_turns == 1


def test_add_turn_rejects_bad_role_and_kind(tmp_path):
    store = _store(tmp_path)
    session = store.create_session()
    with pytest.raises(ValueError):
        store.add_turn(session.id, role="alien", content="x", kind="message")
    with pytest.raises(ValueError):
        store.add_turn(session.id, role="founder", content="x", kind="bogus")


def test_run_id_stamping_inside_scope(tmp_path):
    store = _store(tmp_path)
    with run_scope() as rid:
        session = store.create_session()
        turn = store.add_turn(session.id, role="founder", content="hi")
    assert session.run_id == rid
    assert is_valid_run_id(session.run_id)
    assert turn.run_id == rid


def test_run_id_none_outside_scope(tmp_path):
    store = _store(tmp_path)
    session = store.create_session()
    assert session.run_id is None
    turn = store.add_turn(session.id, role="founder", content="hi")
    assert turn.run_id is None


def test_explicit_run_id_param_overrides(tmp_path):
    store = _store(tmp_path)
    turn_session = store.create_session(run_id="r_OVERRIDE")
    assert turn_session.run_id == "r_OVERRIDE"


def test_lifecycle_transitions(tmp_path):
    store = _store(tmp_path)
    s = store.create_session()
    store.update_session_state(s.id, SessionStatus.CLARIFYING)
    assert store.get_session(s.id).state == SessionStatus.CLARIFYING
    closed = store.update_session_state(
        s.id, SessionStatus.DISPATCHED, route="execute", project_id="p1"
    )
    assert closed.state == SessionStatus.DISPATCHED
    assert closed.route == "execute"
    assert closed.project_id == "p1"
    assert closed.closed_at is not None


def test_terminal_session_rejects_further_transition(tmp_path):
    store = _store(tmp_path)
    s = store.create_session()
    store.update_session_state(s.id, SessionStatus.ANSWERED)
    with pytest.raises(IllegalSessionTransition):
        store.update_session_state(s.id, SessionStatus.DISPATCHED)


def test_gated_only_resolves_to_dispatched_or_abandoned(tmp_path):
    store = _store(tmp_path)
    s = store.create_session()
    store.update_session_state(s.id, SessionStatus.GATED)
    with pytest.raises(IllegalSessionTransition):
        store.update_session_state(s.id, SessionStatus.CLARIFYING)
    resolved = store.update_session_state(s.id, SessionStatus.DISPATCHED)
    assert resolved.state == SessionStatus.DISPATCHED


def test_at_clarify_cap(tmp_path):
    store = _store(tmp_path)
    s = store.create_session()
    assert store.at_clarify_cap(s.id) is False
    for _ in range(MAX_CLARIFY_TURNS):
        store.add_turn(
            s.id, role="ceo", content="q?", kind="clarify_question"
        )
    assert store.get_session(s.id).clarify_turns == MAX_CLARIFY_TURNS
    assert store.at_clarify_cap(s.id) is True


def test_list_sessions_and_open_session(tmp_path):
    store = _store(tmp_path)
    s1 = store.create_session()
    s2 = store.create_session()
    store.update_session_state(s1.id, SessionStatus.ANSWERED)

    all_sessions = store.list_sessions()
    assert {s.id for s in all_sessions} >= {s1.id, s2.id}
    # open_session returns the most-recent non-terminal session.
    assert store.open_session().id == s2.id
    # filter by state
    answered = store.list_sessions(state="answered")
    assert [s.id for s in answered] == [s1.id]


def test_update_unknown_session_returns_none(tmp_path):
    store = _store(tmp_path)
    assert store.update_session_state("nope", SessionStatus.DISPATCHED) is None
    assert store.get_session("nope") is None
