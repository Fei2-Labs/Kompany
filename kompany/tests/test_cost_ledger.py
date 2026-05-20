"""Tests for the LEDGER + STREAM layer of the cost visibility discipline."""

from __future__ import annotations

from kompany.llm.client import LLMResponse
from kompany.llm.cost_ledger import record_ai_cost
from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.models import LedgerCategory


class _FakeHub:
    """In-memory event hub double for tests."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, dict(payload)))


def _make_ledger(tmp_path) -> Ledger:
    db = Database(tmp_path)
    ledger = Ledger(db)
    ledger.record(
        amount=50.0,
        description="Capital",
        category=LedgerCategory.INCOME,
    )
    return ledger


def _make_resp(cost: float = 0.0123, recorded: bool = False) -> LLMResponse:
    return LLMResponse(
        text="ok",
        input_tokens=120,
        output_tokens=80,
        cost_usd=cost,
        model="claude-sonnet-4-20250514",
        _cost_recorded=recorded,
    )


def test_record_ai_cost_writes_ledger_when_not_already_recorded(tmp_path):
    ledger = _make_ledger(tmp_path)
    hub = _FakeHub()
    resp = _make_resp()
    starting_balance = ledger.get_balance()

    record_ai_cost(
        ledger,
        resp,
        action_type="target_feasibility",
        run_id="run-1",
        event_hub=hub,
    )

    # Ledger balance dropped by ~ resp.cost_usd
    assert ledger.get_balance() < starting_balance
    # Mark set so a follow-up call won't double-book.
    assert resp._cost_recorded is True


def test_record_ai_cost_skips_ledger_when_already_recorded(tmp_path):
    ledger = _make_ledger(tmp_path)
    hub = _FakeHub()
    resp = _make_resp(recorded=True)
    starting_balance = ledger.get_balance()

    record_ai_cost(
        ledger,
        resp,
        action_type="distillation",
        event_hub=hub,
    )

    # Ledger NOT touched
    assert ledger.get_balance() == starting_balance
    # SSE still fires (we want the UI to learn the action_type label)
    assert any(t == "llm.spend" for t, _ in hub.events)


def test_record_ai_cost_emits_sse_payload(tmp_path):
    ledger = _make_ledger(tmp_path)
    hub = _FakeHub()
    resp = _make_resp(cost=0.025)

    record_ai_cost(
        ledger,
        resp,
        action_type="debate_round_1",
        run_id="run-42",
        event_hub=hub,
    )

    assert len(hub.events) == 1
    event_type, payload = hub.events[0]
    assert event_type == "llm.spend"
    assert payload["action_type"] == "debate_round_1"
    assert payload["model"] == resp.model
    assert payload["input_tokens"] == 120
    assert payload["output_tokens"] == 80
    assert payload["cost_usd"] == 0.025
    assert payload["run_id"] == "run-42"
    assert payload["ledger_balance_after"] is not None


def test_record_ai_cost_no_event_hub_is_silent(tmp_path):
    ledger = _make_ledger(tmp_path)
    resp = _make_resp()

    # Must not raise even with event_hub=None
    record_ai_cost(
        ledger,
        resp,
        action_type="other",
        event_hub=None,
    )
    assert resp._cost_recorded is True


def test_double_record_does_not_double_book(tmp_path):
    """Calling record_ai_cost twice yields exactly one AI_COST row."""
    ledger = _make_ledger(tmp_path)
    resp = _make_resp(cost=0.05)

    record_ai_cost(ledger, resp, action_type="other")
    record_ai_cost(ledger, resp, action_type="other")

    rows = ledger.db.execute(
        "SELECT COUNT(*) AS n FROM ledger WHERE category = 'ai_cost'"
    ).fetchone()
    assert rows["n"] == 1


def test_record_ai_cost_handles_ledger_none(tmp_path):
    """Pure SSE path: ledger=None, event_hub wired."""
    hub = _FakeHub()
    resp = _make_resp()
    record_ai_cost(None, resp, action_type="ping", event_hub=hub)
    assert len(hub.events) == 1
    assert hub.events[0][1]["ledger_balance_after"] is None


def test_cost_tracker_emits_llm_spend_on_record(tmp_path):
    """CostTracker.record fires llm.spend automatically when hub is wired."""
    from kompany.llm.cost_tracker import CostTracker

    ledger = _make_ledger(tmp_path)
    hub = _FakeHub()
    tracker = CostTracker(ledger=ledger, event_hub=hub)
    tracker.record(
        model="claude-sonnet-4-20250514",
        input_tokens=100,
        output_tokens=50,
        description="test call",
        action_type="agent_classify",
    )
    spend_events = [p for t, p in hub.events if t == "llm.spend"]
    assert len(spend_events) == 1
    assert spend_events[0]["action_type"] == "agent_classify"
    assert spend_events[0]["input_tokens"] == 100
    assert spend_events[0]["output_tokens"] == 50
    assert spend_events[0]["cost_usd"] > 0


def test_cost_tracker_defaults_action_type_other(tmp_path):
    """Missing action_type label falls back to 'other'."""
    from kompany.llm.cost_tracker import CostTracker

    ledger = _make_ledger(tmp_path)
    hub = _FakeHub()
    tracker = CostTracker(ledger=ledger, event_hub=hub)
    tracker.record(
        model="claude-sonnet-4-20250514",
        input_tokens=10,
        output_tokens=5,
        description="anonymous",
    )
    assert hub.events[-1][1]["action_type"] == "other"


def test_cost_tracker_no_hub_no_crash(tmp_path):
    """Existing constructor (no event_hub kwarg) must keep working."""
    from kompany.llm.cost_tracker import CostTracker

    ledger = _make_ledger(tmp_path)
    tracker = CostTracker(ledger=ledger)  # legacy 1-arg form
    cost = tracker.record(
        model="claude-sonnet-4-20250514",
        input_tokens=10,
        output_tokens=5,
        description="legacy",
    )
    assert cost > 0
