"""PR1 unit tests: PROPOSED state machine + ConversationStore.recent_turns.

Covers:
* SessionStatus.PROPOSED exists and is not in SESSION_TERMINAL_STATUSES
* State machine: PROPOSED -> dispatched / abandoned allowed; other targets raise
* ConversationStore.recent_turns returns cross-session turns, oldest-first
* recent_turns filters to message/final kinds only
* recent_turns respects the limit
"""
from __future__ import annotations

import pytest

from kompany.state.conversation import ConversationStore, IllegalSessionTransition
from kompany.state.database import Database
from kompany.state.models import SESSION_TERMINAL_STATUSES, SessionStatus


# -----------------------------------------------------------------------
# SessionStatus.PROPOSED — enum value + terminal-set membership
# -----------------------------------------------------------------------

def test_proposed_status_exists():
    assert SessionStatus.PROPOSED == "proposed"


def test_proposed_not_terminal():
    assert SessionStatus.PROPOSED not in SESSION_TERMINAL_STATUSES


def test_answered_still_terminal():
    assert SessionStatus.ANSWERED in SESSION_TERMINAL_STATUSES


# -----------------------------------------------------------------------
# State machine transitions for PROPOSED
# -----------------------------------------------------------------------

def _store(tmp_path):
    db = Database(tmp_path)
    return ConversationStore(db)


def _open_session(store):
    s = store.create_session()
    return s.id


def _advance_to_proposed(store, session_id):
    store.update_session_state(session_id, SessionStatus.PROPOSED, route="answer")


def test_open_to_proposed_allowed(tmp_path):
    store = _store(tmp_path)
    sid = _open_session(store)
    # Should not raise
    store.update_session_state(sid, SessionStatus.PROPOSED, route="answer")
    assert store.get_session(sid).state == SessionStatus.PROPOSED


def test_proposed_to_dispatched_allowed(tmp_path):
    store = _store(tmp_path)
    sid = _open_session(store)
    _advance_to_proposed(store, sid)
    store.update_session_state(sid, SessionStatus.DISPATCHED, route="answer")
    assert store.get_session(sid).state == SessionStatus.DISPATCHED


def test_proposed_to_abandoned_allowed(tmp_path):
    store = _store(tmp_path)
    sid = _open_session(store)
    _advance_to_proposed(store, sid)
    store.update_session_state(sid, SessionStatus.ABANDONED, route="answer")
    assert store.get_session(sid).state == SessionStatus.ABANDONED


def test_proposed_to_clarifying_raises(tmp_path):
    store = _store(tmp_path)
    sid = _open_session(store)
    _advance_to_proposed(store, sid)
    with pytest.raises(IllegalSessionTransition, match="proposed session"):
        store.update_session_state(sid, SessionStatus.CLARIFYING)


def test_proposed_to_answered_raises(tmp_path):
    store = _store(tmp_path)
    sid = _open_session(store)
    _advance_to_proposed(store, sid)
    with pytest.raises(IllegalSessionTransition, match="proposed session"):
        store.update_session_state(sid, SessionStatus.ANSWERED)


def test_dispatched_to_proposed_raises(tmp_path):
    """Terminal DISPATCHED cannot transition anywhere."""
    store = _store(tmp_path)
    sid = _open_session(store)
    store.update_session_state(sid, SessionStatus.DISPATCHED, route="execute")
    with pytest.raises(IllegalSessionTransition, match="terminal state"):
        store.update_session_state(sid, SessionStatus.PROPOSED)


# -----------------------------------------------------------------------
# ConversationStore.recent_turns
# -----------------------------------------------------------------------

def test_recent_turns_empty(tmp_path):
    store = _store(tmp_path)
    assert store.recent_turns(6) == []


def test_recent_turns_single_session(tmp_path):
    store = _store(tmp_path)
    sid = _open_session(store)
    store.add_turn(sid, role="founder", content="q1", kind="message")
    store.add_turn(sid, role="ceo", content="a1", kind="final")
    turns = store.recent_turns(6)
    assert len(turns) == 2
    assert turns[0].role == "founder"
    assert turns[1].role == "ceo"


def test_recent_turns_cross_session_oldest_first(tmp_path):
    store = _store(tmp_path)
    s1 = _open_session(store)
    store.add_turn(s1, role="founder", content="old question", kind="message")
    s2 = _open_session(store)
    store.add_turn(s2, role="founder", content="new question", kind="message")
    turns = store.recent_turns(6)
    assert turns[0].content == "old question"
    assert turns[1].content == "new question"


def test_recent_turns_respects_limit(tmp_path):
    store = _store(tmp_path)
    sid = _open_session(store)
    for i in range(10):
        store.add_turn(sid, role="founder", content=f"q{i}", kind="message")
    turns = store.recent_turns(4)
    assert len(turns) == 4
    # Limit keeps the MOST RECENT 4, then reverses to oldest-first.
    assert turns[-1].content == "q9"


def test_recent_turns_filters_noise_kinds(tmp_path):
    store = _store(tmp_path)
    sid = _open_session(store)
    store.add_turn(sid, role="founder", content="msg", kind="message")
    store.add_turn(sid, role="ceo", content="preview", kind="preview")
    store.add_turn(sid, role="ceo", content="clarify?", kind="clarify_question")
    store.add_turn(sid, role="ceo", content="final", kind="final")
    turns = store.recent_turns(10)
    kinds = [t.kind for t in turns]
    assert "preview" not in kinds
    assert "clarify_question" not in kinds
    assert "message" in kinds
    assert "final" in kinds
