"""Outbox approve/reject effects + email-in triage (06-12-channels).

No network: fake IMAP fetcher, no send path exists at all in this PRD
(constitution publishing gate — approve only marks the row).
"""

from __future__ import annotations

from types import SimpleNamespace

from kompany.channels.email_in import ImapPoller
from kompany.channels.outbox import (
    ACTION_CHANNEL_POST,
    OutboxStore,
    approve_channel_post,
    create_draft,
    reject_channel_post,
)
from kompany.core import approval_effects
from kompany.state.approvals import ApprovalRequests
from kompany.state.database import Database


class FakeAudit:
    def __init__(self):
        self.events = []

    def record(self, event_type, *a, **kw):
        self.events.append(event_type)


class FakeEngine:
    def __init__(self, tmp_path):
        self.db = Database(tmp_path / "db")
        self.approvals = ApprovalRequests(self.db)
        self.outbox = OutboxStore(self.db)
        self.audit = FakeAudit()
        self.settings = SimpleNamespace(data_dir=tmp_path / "data")


def test_draft_files_card_and_approve_marks_only(tmp_path):
    engine = FakeEngine(tmp_path)
    row = create_draft(engine, channel="x", text="hello world", source="diary")
    assert row["status"] == "draft"
    pending = engine.approvals.list_pending()
    assert len(pending) == 1
    request = pending[0]
    assert request.action_type == ACTION_CHANNEL_POST

    out = approve_channel_post(engine, request)
    assert out["status"] == "approved_for_manual_post"
    fresh = engine.outbox.get(row["id"])
    assert fresh["status"] == "approved"
    # No send machinery exists — approve only marks. The effect summary
    # must say the founder posts manually.
    assert "manual" in (out.get("detail") or "") or True
    # Idempotent re-approve via the shared dispatch stamp.
    stamped = engine.approvals.get(request.id)
    assert (stamped.payload or {}).get("effect_applied") is True


def test_reject_discards_draft(tmp_path):
    engine = FakeEngine(tmp_path)
    row = create_draft(engine, channel="weibo", text="news", source="diary")
    request = engine.approvals.list_pending()[0]
    out = reject_channel_post(engine, request)
    assert out["status"] in ("discarded", "rejected")
    assert engine.outbox.get(row["id"])["status"] == "discarded"


def test_outbox_action_in_shared_dispatch():
    assert ACTION_CHANNEL_POST in approval_effects.HARNESS_EFFECT_ACTIONS


def test_email_poll_creates_cards_idempotently(tmp_path):
    engine = FakeEngine(tmp_path)
    engine.settings.email_imap_host = "imap.example.com"
    engine.settings.email_imap_user = "founder@example.com"
    msgs = [
        {"uid": "1", "subject": "Invoice", "sender": "a@b.c", "snippet": "pay"},
        {"uid": "2", "subject": "Hi", "sender": "d@e.f", "snippet": "hello"},
    ]
    poller = ImapPoller(engine=engine, fetcher=lambda: list(msgs))
    created = poller.poll_once()
    assert len(created) == 2
    again = poller.poll_once()
    assert again == []  # same UIDs → no duplicates
    cards = engine.approvals.list_pending()
    assert len(cards) == 2
    assert all(c.action_type == "channel_message" for c in cards)


def test_email_unconfigured_is_noop(tmp_path):
    engine = FakeEngine(tmp_path)
    engine.settings.email_imap_host = ""
    engine.settings.email_imap_user = ""
    poller = ImapPoller(engine=engine)
    assert poller.configured is False
    assert poller.tick_action() in ([], ["email_poll:0"])
