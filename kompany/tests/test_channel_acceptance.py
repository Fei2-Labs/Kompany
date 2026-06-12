"""CEO-channel acceptance / edge tests (06-03-ceo-channel PR6).

These fill the PRD Acceptance-Criteria gaps not covered by the PR1–PR5 unit
tests (route detection, lifecycle, clarify cap, threshold gate, REST flatten
parity).  Specifically:

* Engine suspended → ``status=suspended`` flows through as a CEO reply and the
  session is NOT left stuck (re-sendable once resumed).
* Concurrent sessions → two ``process_directive`` calls on distinct sessions
  keep independent ``run_total`` cost AND independent session/turn state (no
  cross-contamination of turns).
* Mid-execution reload restore → a gated session persisted, a brand-new engine
  instance (restart), the REST ``GET /channel/sessions/{id}`` restore path
  returns the full thread, and ``POST /channel/sessions/{id}/go`` works.
* Full clarify → converge → dispatch acceptance at the REST level (ambiguous
  send → ``clarify`` status → reply on same session → ``completed``).

LLM is always mocked (a FakeCEO that returns scripted classifications) per
testing-guidelines.md — no network, no provider keys exercised.
"""

from __future__ import annotations

import threading

import pytest

from kompany.agents.ceo import DirectiveClassification
from kompany.state.models import SessionStatus

# Reuse the engine builder + FakeCEO installer from the PR1 engine tests so the
# wiring stays in one place (the harness-shaped engine fixture is non-trivial).
from tests.test_engine_channel import _build_engine, _install_ceo


# ----------------------------------------------------------------------
# Engine suspended → suspended renders as a CEO reply, session not stuck.
# ----------------------------------------------------------------------

def test_suspended_engine_send_returns_suspended_and_session_resendable(
    tmp_path, monkeypatch
):
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    engine.suspend("quota_exhausted")

    # No CEO classification is consumed while suspended (the early return path
    # fires before classify) — install an empty queue to prove it.
    _install_ceo(engine, [])

    result = engine.process_directive("ship the beta")

    # The suspended status flows through to the founder as a result.
    assert result.status == "suspended"
    assert "suspended" in result.message.lower()
    assert result.session_id is not None
    # run_id is still stamped (the early return is inside run_scope).
    assert result.run_id is not None

    # The session is NOT stuck in a terminal state — it stays open so the
    # founder can re-send the same intent once the engine resumes.
    session = engine.channel.get_session(result.session_id)
    assert session.state not in {
        SessionStatus.DISPATCHED,
        SessionStatus.ANSWERED,
        SessionStatus.ABANDONED,
    }

    # After resume the same session accepts a send and dispatches normally.
    engine.resume()
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="clear now",
        primary_squad="strategy",
        approval_tier="auto",
        route="execute",
    )])
    engine._handle_operational = lambda d, c, ceo: __import__(
        "kompany.core.directive", fromlist=["DirectiveResult"]
    ).DirectiveResult(directive=d, status="completed", message="done",
                      agents_used=["ceo"])

    resumed = engine.process_directive(
        "ship the beta", session_id=result.session_id
    )
    assert resumed.status == "completed"
    assert resumed.session_id == result.session_id
    assert engine.channel.get_session(
        result.session_id
    ).state == SessionStatus.DISPATCHED


# ----------------------------------------------------------------------
# Concurrent sessions — independent cost AND independent session/turn state.
# ----------------------------------------------------------------------

def test_concurrent_sessions_isolate_cost_and_turns(tmp_path, monkeypatch):
    """Two ``process_directive`` calls on distinct sessions keep independent
    run cost AND independent session/turn threads — no cross-contamination.

    PR0 (``test_run_total_concurrent_threads_do_not_cross_contaminate``)
    already proves per-run cost isolation under true thread simultaneity at the
    CostTracker level. Here the value-add is asserting the FULL directive path:
    each session's own ``run_total`` and its own turn thread. The two sends run
    on threads but serialize the single shared SQLite connection with a lock
    (the engine never shares one cursor mid-statement across threads in
    production) — independence is a function of per-thread ``run_scope``
    ContextVars + session_id, not of writer simultaneity.
    """
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)

    def make_classification():
        return DirectiveClassification(
            directive_type="operational",
            reasoning="clear",
            primary_squad="strategy",
            approval_tier="auto",
            route="execute",
        )

    # FakeCEO must be thread-safe: hand each classify call a fresh object.
    class ConcurrentCEO:
        def classify(self, raw_input, directive_id=None, targets_summary=None,
                     glossary_summary=None, session_context=None,
                     clarify_capped=False, **kwargs):
            return make_classification()

    fake = ConcurrentCEO()
    original = engine.registry

    class FakeRegistry:
        def get(self, role, company_state=None):
            return fake if role == "ceo" else original.get(role, company_state)

    engine.registry = FakeRegistry()

    # Per-session distinct spend: alpha books 3 units, beta books 5.
    spend_map = {"alpha": 3, "beta": 5}

    def handler(d, c, ceo):
        from kompany.core.directive import DirectiveResult
        units = spend_map[d.raw_input]
        for _ in range(units):
            engine.cost_tracker.record(
                "claude-sonnet-4-20250514", 1000, 500, "exec", directive_id=d.id
            )
        return DirectiveResult(
            directive=d,
            status="completed",
            message=f"done {d.raw_input}",
            total_ai_cost=engine.cost_tracker.run_total(),
            agents_used=["ceo"],
        )

    engine._handle_operational = handler

    results: dict[str, object] = {}
    db_lock = threading.Lock()
    start = threading.Barrier(2)

    def run(name):
        start.wait()
        # Serialize the shared connection — the run_scope/ContextVar isolation
        # being asserted is orthogonal to writer simultaneity.
        with db_lock:
            results[name] = engine.process_directive(name)

    t1 = threading.Thread(target=run, args=("alpha",))
    t2 = threading.Thread(target=run, args=("beta",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    r_alpha = results["alpha"]
    r_beta = results["beta"]

    # Independent sessions + run ids.
    assert r_alpha.session_id != r_beta.session_id
    assert r_alpha.run_id != r_beta.run_id

    # Independent cost: beta booked more spend than alpha; neither sums to the
    # combined total (would be the cross-contamination bug PR0 killed).
    assert r_beta.total_ai_cost > r_alpha.total_ai_cost > 0.0
    combined = r_alpha.total_ai_cost + r_beta.total_ai_cost
    assert r_alpha.total_ai_cost < combined
    assert r_beta.total_ai_cost < combined

    # Independent turn threads: each session has exactly its own message+final
    # pair, no leakage of the other session's turns.
    alpha_turns = engine.channel.session_turns(r_alpha.session_id)
    beta_turns = engine.channel.session_turns(r_beta.session_id)
    assert [t.kind for t in alpha_turns] == ["message", "final"]
    assert [t.kind for t in beta_turns] == ["message", "final"]
    assert alpha_turns[0].content == "alpha"
    assert beta_turns[0].content == "beta"
    # The recorded final-turn cost matches the session's own run total.
    assert alpha_turns[-1].cost == pytest.approx(r_alpha.total_ai_cost)
    assert beta_turns[-1].cost == pytest.approx(r_beta.total_ai_cost)
    # Both sessions dispatched independently.
    assert engine.channel.get_session(
        r_alpha.session_id
    ).state == SessionStatus.DISPATCHED
    assert engine.channel.get_session(
        r_beta.session_id
    ).state == SessionStatus.DISPATCHED


def test_clarify_reply_works_while_another_session_executes(tmp_path, monkeypatch):
    """A clarify reply on one session is never blocked by a concurrent
    executing session (PRD: "clarify reply works while another session
    executes"). Sequential here — the point is the open clarify session stays
    resolvable after an unrelated session has dispatched."""
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)

    # Session A: open a clarify and leave it parked.
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="ambiguous",
        primary_squad="strategy",
        approval_tier="auto",
        route="clarify",
        clarify_question="Which platform?",
    )])
    clar = engine.process_directive("build the app")
    assert clar.status == "clarify"

    # Session B: an unrelated directive executes to completion meanwhile.
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="clear",
        primary_squad="strategy",
        approval_tier="auto",
        route="execute",
    )])
    engine._handle_operational = lambda d, c, ceo: __import__(
        "kompany.core.directive", fromlist=["DirectiveResult"]
    ).DirectiveResult(directive=d, status="completed", message="done",
                      agents_used=["ceo"])
    other = engine.process_directive("send the newsletter")
    assert other.status == "completed"
    assert other.session_id != clar.session_id

    # The parked clarify session is still resolvable.
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="clear now",
        primary_squad="strategy",
        approval_tier="auto",
        route="execute",
    )])
    reply = engine.process_directive("iOS", session_id=clar.session_id)
    assert reply.status == "completed"
    assert reply.session_id == clar.session_id
    assert engine.channel.get_session(
        clar.session_id
    ).state == SessionStatus.DISPATCHED


# ----------------------------------------------------------------------
# REST-level acceptance — full real engine wired into the FastAPI app.
# ----------------------------------------------------------------------

def _trivial_dispatch(engine):
    """No-LLM operational handler that books a small run cost."""
    def handler(d, c, ceo):
        from kompany.core.directive import DirectiveResult
        engine.cost_tracker.record(
            "claude-sonnet-4-20250514", 1000, 500, "exec", directive_id=d.id
        )
        return DirectiveResult(
            directive=d, status="completed", message="done",
            total_ai_cost=engine.cost_tracker.run_total(), agents_used=["ceo"],
        )
    engine._handle_operational = handler


def test_rest_clarify_then_converge_to_dispatch(tmp_path, monkeypatch):
    """Full clarify → reply → dispatch acceptance through the REST surface:
    POST /channel/send (ambiguous) → status=clarify + session_id, then
    POST /channel/send with that session_id → status=completed."""
    from fastapi.testclient import TestClient

    from kompany.interfaces.api import app

    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    fake = _install_ceo(engine, [
        DirectiveClassification(
            directive_type="operational",
            reasoning="ambiguous",
            primary_squad="strategy",
            approval_tier="auto",
            route="clarify",
            clarify_question="Which segment — B2B or B2C?",
        ),
        DirectiveClassification(
            directive_type="operational",
            reasoning="clear now",
            primary_squad="strategy",
            approval_tier="auto",
            route="execute",
        ),
    ])
    _trivial_dispatch(engine)
    monkeypatch.setattr("kompany.interfaces.api._engine", engine)
    client = TestClient(app)

    r1 = client.post("/channel/send", json={"text": "build a CRM"})
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["status"] == "clarify"
    assert b1["message"] == "Which segment — B2B or B2C?"
    sid = b1["session_id"]
    assert sid

    # The thread now shows the founder message + CEO clarify question.
    detail = client.get(f"/channel/sessions/{sid}").json()
    assert [t["kind"] for t in detail["turns"]] == ["message", "clarify_question"]

    r2 = client.post("/channel/send", json={"text": "B2B", "session_id": sid})
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["status"] == "completed"
    assert b2["session_id"] == sid
    # The reply was routed into the SAME session (prior turns are context).
    assert "Which segment" in (fake.last_context or "")

    # The full converged thread is readable via REST.
    final = client.get(f"/channel/sessions/{sid}").json()
    assert [t["kind"] for t in final["turns"]] == [
        "message", "clarify_question", "message", "final"
    ]
    assert final["session"]["state"] == "dispatched"


def test_rest_gated_session_restores_after_restart_then_go(tmp_path, monkeypatch):
    """Mid-execution reload / restart restore at the REST level: a gated
    session is persisted, a BRAND-NEW engine instance (restart) is wired into
    the API, GET /channel/sessions/{id} returns the full thread (restore path),
    and POST /channel/sessions/{id}/go executes the held directive without
    re-classifying (no CEO installed on the restarted instance)."""
    from fastapi.testclient import TestClient

    from kompany.interfaces.api import app

    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    _install_ceo(engine, [DirectiveClassification(
        directive_type="operational",
        reasoning="big spend",
        primary_squad="strategy",
        approval_tier="master",
        estimated_cost_eur=0.0,
        route="execute",
        execution_plan="Run the paid campaign.",
    )])
    _trivial_dispatch(engine)
    monkeypatch.setattr("kompany.interfaces.api._engine", engine)
    client = TestClient(app)

    gated = client.post("/channel/send", json={"text": "launch campaign"}).json()
    assert gated["status"] == "gated"
    sid = gated["session_id"]

    # Simulate an engine restart: a brand-new instance on the SAME data dir.
    # Its in-memory gated-directive cache is empty; the snapshot lives on the
    # persisted session row.
    restarted = _build_engine(tmp_path, monkeypatch, initialize=False)
    assert not getattr(restarted, "_gated_directives", {})
    _trivial_dispatch(restarted)
    monkeypatch.setattr("kompany.interfaces.api._engine", restarted)

    # REST restore path: the full thread comes back after the restart.
    detail = client.get(f"/channel/sessions/{sid}").json()
    assert detail["session"]["session_id"] == sid
    assert detail["session"]["state"] == "gated"
    assert [t["kind"] for t in detail["turns"]] == ["message", "preview"]
    preview = detail["turns"][1]
    assert "Run the paid campaign." in preview["content"]

    # GO through REST executes the held snapshot (no re-classify — there is no
    # CEO installed on the restarted instance, so a re-classify would fail).
    go = client.post(f"/channel/sessions/{sid}/go").json()
    assert go["status"] == "completed"
    assert go["session_id"] == sid

    after = client.get(f"/channel/sessions/{sid}").json()
    assert after["session"]["state"] == "dispatched"
    assert [t["kind"] for t in after["turns"]] == ["message", "preview", "final"]
    # The final turn records the ACTUAL run cost (> 0 from the trivial spend).
    assert after["turns"][-1]["cost"] > 0.0
