"""Telegram channel adapter tests (06-12-channels PR1).

Fake transport everywhere — NO network, NO real LLM. The adapter
contract (PRD D1) is exercised against a scripted ``process_directive``
plus a real Database / ConversationStore so session mapping and replay
protection hit real SQL.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from kompany.channels.telegram import GATED_HINT, REPLAY_SOURCE, TelegramWorker
from kompany.core.directive import Directive, DirectiveResult
from kompany.state.channel_sessions_map import ChannelSessionMapStore
from kompany.state.conversation import ConversationStore
from kompany.state.database import Database
from kompany.state.models import SessionStatus
from kompany.state.remote_replay import RemoteReplayStore


class FakeTransport:
    """Records every call; serves queued getUpdates batches."""

    def __init__(self, batches=None):
        self.batches = list(batches or [])
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        if method == "getUpdates":
            return {"ok": True, "result": self.batches.pop(0) if self.batches else []}
        return {"ok": True, "result": {"message_id": 1}}

    @property
    def sent(self) -> list[dict]:
        return [p for m, p in self.calls if m == "sendMessage"]


class FakeEngine:
    """Minimal engine surface for channels/telegram.py."""

    def __init__(self, tmp_path, script=None, allowed="111"):
        self.db = Database(tmp_path / "db")
        self.channel = ConversationStore(self.db)
        self.remote_replay = RemoteReplayStore(self.db)
        self.settings = SimpleNamespace(
            telegram_bot_token="tok",
            telegram_allowed_chat_ids=allowed,
        )
        self.script = list(script or [])
        self.directive_calls: list[tuple[str, str | None]] = []

    def process_directive(self, text, session_id=None):
        self.directive_calls.append((text, session_id))
        return self.script.pop(0)


def _result(status, message, session_id):
    return DirectiveResult(
        directive=Directive(raw_input="x"),
        status=status,
        message=message,
        session_id=session_id,
    )


def _update(update_id, chat_id, text):
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id}, "text": text},
    }


def test_message_becomes_directive_and_reply(tmp_path):
    engine = FakeEngine(tmp_path)
    session = engine.channel.create_session()
    engine.channel.update_session_state(session.id, SessionStatus.DISPATCHED)
    engine.script = [_result("completed", "Done: shipped.", session.id)]
    transport = FakeTransport([[_update(1, 111, "ship the landing page")]])
    worker = TelegramWorker(engine, transport=transport)

    outcomes = worker.poll_once()

    assert engine.directive_calls == [("ship the landing page", None)]
    assert outcomes[0]["status"] == "completed"
    assert transport.sent[0]["chat_id"] == "111"
    assert "Done: shipped." in transport.sent[0]["text"]
    # Terminal result → no lingering session mapping.
    assert ChannelSessionMapStore(engine.db).get("111") is None
    assert worker.offset == 2


def test_clarify_round_trip_keeps_session(tmp_path):
    engine = FakeEngine(tmp_path)
    session = engine.channel.create_session()
    engine.channel.update_session_state(session.id, SessionStatus.CLARIFYING)
    engine.script = [
        _result("clarify", "Which product?", session.id),
        _result("completed", "Done.", session.id),
    ]
    transport = FakeTransport(
        [[_update(1, 111, "launch it")], [_update(2, 111, "the cli tool")]]
    )
    worker = TelegramWorker(engine, transport=transport)

    worker.poll_once()
    assert ChannelSessionMapStore(engine.db).get("111") == session.id
    assert "Which product?" in transport.sent[0]["text"]

    worker.poll_once()
    # Second message continued the SAME session.
    assert engine.directive_calls[1] == ("the cli tool", session.id)
    assert ChannelSessionMapStore(engine.db).get("111") is None


def test_unauthorized_chat_ignored(tmp_path):
    engine = FakeEngine(tmp_path, allowed="111")
    transport = FakeTransport([[_update(1, 999, "drain the treasury")]])
    worker = TelegramWorker(engine, transport=transport)

    outcomes = worker.poll_once()

    assert outcomes == [{"status": "ignored", "reason": "unauthorized"}]
    assert engine.directive_calls == []
    assert transport.sent == []  # never reply to strangers


def test_empty_allowlist_ignores_everyone(tmp_path):
    engine = FakeEngine(tmp_path, allowed="")
    worker = TelegramWorker(
        engine, transport=FakeTransport([[_update(1, 111, "hi")]])
    )
    assert worker.poll_once()[0]["reason"] == "unauthorized"
    assert engine.directive_calls == []


def test_duplicate_update_id_replay_ignored(tmp_path):
    engine = FakeEngine(tmp_path)
    session = engine.channel.create_session()
    engine.channel.update_session_state(session.id, SessionStatus.DISPATCHED)
    engine.script = [_result("completed", "Done.", session.id)]
    same = _update(7, 111, "do it")
    transport = FakeTransport([[same], [same]])
    worker = TelegramWorker(engine, transport=transport)

    worker.poll_once()
    outcomes = worker.poll_once()

    assert len(engine.directive_calls) == 1
    assert outcomes == [{"status": "ignored", "reason": "replayed"}]
    assert engine.remote_replay.get(REPLAY_SOURCE, "7") is not None


def test_go_flow_on_gated_session(tmp_path):
    engine = FakeEngine(tmp_path)
    session = engine.channel.create_session()
    engine.script = [
        _result("gated", "This costs $4. Preview attached.", session.id),
        _result("completed", "Executed.", session.id),
    ]
    transport = FakeTransport(
        [[_update(1, 111, "build the site")], [_update(2, 111, "GO")]]
    )
    worker = TelegramWorker(engine, transport=transport)

    worker.poll_once()
    # Gated reply carries the GO hint and the session stays mapped.
    assert GATED_HINT.strip() in transport.sent[0]["text"]
    engine.channel.update_session_state(session.id, SessionStatus.GATED)
    assert ChannelSessionMapStore(engine.db).get("111") == session.id

    worker.poll_once()
    # GO routed into the SAME gated session (engine's GO branch handles it).
    assert engine.directive_calls[1] == ("GO", session.id)
    assert "Executed." in transport.sent[1]["text"]


def test_stale_mapping_to_closed_session_resets(tmp_path):
    engine = FakeEngine(tmp_path)
    closed = engine.channel.create_session()
    engine.channel.update_session_state(closed.id, SessionStatus.ABANDONED)
    ChannelSessionMapStore(engine.db).set("111", closed.id)
    engine.script = [_result("completed", "Fresh.", "new-session")]
    worker = TelegramWorker(
        engine, transport=FakeTransport([[_update(1, 111, "hello again")]])
    )

    worker.poll_once()

    # Closed session was NOT continued — a fresh one was opened.
    assert engine.directive_calls == [("hello again", None)]


def test_worker_start_stop_idempotent(tmp_path):
    engine = FakeEngine(tmp_path)
    worker = TelegramWorker(
        engine, transport=FakeTransport(), idle_sleep_seconds=0
    )

    async def scenario():
        worker.start()
        first = worker._task
        worker.start()  # idempotent — same task
        assert worker._task is first
        assert worker.running
        await worker.stop()
        await worker.stop()  # idempotent
        assert not worker.running

    asyncio.run(scenario())


def test_engine_start_wires_telegram_worker(tmp_path, monkeypatch):
    """engine.start() starts the worker iff telegram_bot_token is set."""
    from kompany.core.engine import KompanyEngine

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")
    engine = KompanyEngine()
    assert engine.telegram_worker is not None
    # Never let the test touch the real Telegram API.
    engine.telegram_worker._transport = FakeTransport()

    async def scenario():
        await engine.start()
        assert engine.telegram_worker.running
        await engine.stop()
        assert not engine.telegram_worker.running

    asyncio.run(scenario())


def test_engine_without_token_has_no_worker():
    from kompany.core.engine import KompanyEngine

    engine = KompanyEngine()
    assert engine.telegram_worker is None
