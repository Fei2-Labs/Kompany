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
        self.cancelled_delegations = []
        self.resumed_credential_tasks = []
        self.delegation = SimpleNamespace(
            id="d-1",
            status=SimpleNamespace(value="active"),
            children=[
                SimpleNamespace(
                    assigned_agent="cmo",
                    status=SimpleNamespace(value="completed"),
                    result={"cost": 0.02},
                ),
            ],
        )
        self.contexts = []

    def process_directive(self, text, session_id=None, context=None):
        self.directive_calls.append((text, session_id))
        self.contexts.append(context)
        return self.script.pop(0)

    def cancel_delegation(self, delegation_id):
        self.cancelled_delegations.append(delegation_id)
        return SimpleNamespace(status=SimpleNamespace(value="cancelled"))

    def get_delegation(self, delegation_id):
        return self.delegation if delegation_id == self.delegation.id else None

    def resume_credential_task(self, task_id, action_id):
        self.resumed_credential_tasks.append((task_id, action_id))
        return self.delegation


def _result(status, message, session_id):
    return DirectiveResult(
        directive=Directive(raw_input="x"),
        status=status,
        message=message,
        session_id=session_id,
    )


def test_delegation_progress_edits_status_and_sends_final_ceo_synthesis(tmp_path):
    delegated = _result("delegated", "Background review started.", "s-delegated")
    delegated.project_id = "vinted"
    delegated.active_agent_id = "ceo"
    delegated.agents_used = ["ceo", "cmo", "cfo"]
    delegated.conversation_continues = True
    delegated.delegation_id = "d-1"
    delegated.delegation_status = "queued"
    update = _update(1, 111, "review performance and budget")
    update["message"]["message_thread_id"] = 44
    transport = FakeTransport([[update]])
    worker = TelegramWorker(
        FakeEngine(tmp_path, script=[delegated]),
        transport=transport,
    )

    worker.poll_once()
    worker.handle_delegation_event({
        "delegation_id": "d-1",
        "status": "active",
        "completed_tasks": 1,
        "total_tasks": 2,
    })
    worker.handle_delegation_event({
        "delegation_id": "d-1",
        "status": "completed",
        "message": "Campaign is improving and budget remains on plan.",
    })

    edits = [
        params for method, params in transport.calls
        if method == "editMessageText"
    ]
    assert "Agents: CMO, CFO" in transport.sent[0]["text"]
    assert edits[-1]["chat_id"] == "111"
    assert edits[-1]["message_id"] == 1
    assert "Completed" in edits[-1]["text"]
    assert "Agents: CMO, CFO" in edits[-1]["text"]
    assert len(transport.sent) == 2
    assert transport.sent[-1]["text"].startswith("CEO · vinted")
    assert "Campaign is improving" in transport.sent[-1]["text"]
    assert transport.sent[-1]["message_thread_id"] == "44"


def test_credential_action_edits_progress_and_resumes_original_task(tmp_path):
    delegated = _result("delegated", "Background work started.", "s-delegated")
    delegated.project_id = "vinted"
    delegated.active_agent_id = "ceo"
    delegated.agents_used = ["ceo", "cmo"]
    delegated.conversation_continues = True
    delegated.delegation_id = "d-1"
    delegated.delegation_status = "queued"
    engine = FakeEngine(tmp_path, script=[delegated])
    transport = FakeTransport([[_update(1, 111, "operate vinted")]])
    worker = TelegramWorker(engine, transport=transport)
    worker.poll_once()

    worker.handle_credential_action_event({
        "delegation_id": "d-1",
        "task_id": "t-1",
        "action": {
            "id": "a-1",
            "kind": "reauth",
            "reason": "The Vinted browser session expired.",
            "action_url": "https://broker.example/actions/a-1",
        },
    })

    edit = [
        params for method, params in transport.calls
        if method == "editMessageText"
    ][-1]
    assert "Pending action: Re-authenticate" in edit["text"]
    buttons = [
        button
        for row in edit["reply_markup"]["inline_keyboard"]
        for button in row
    ]
    assert any(button.get("url") == "https://broker.example/actions/a-1"
               for button in buttons)
    retry = next(button for button in buttons if button["text"] == "Retry")

    outcome = worker.handle_update({
        "update_id": 2,
        "callback_query": {
            "id": "cb-credential",
            "data": retry["callback_data"],
            "message": {
                "message_id": 1,
                "chat": {"id": 111},
            },
        },
    })

    assert engine.resumed_credential_tasks == [("t-1", "a-1")]
    assert outcome["status"] == "active"


def test_credential_retry_rejects_other_sender_in_allowed_group(tmp_path):
    delegated = _result("delegated", "Background work started.", "s-delegated")
    delegated.project_id = "vinted"
    delegated.active_agent_id = "ceo"
    delegated.agents_used = ["ceo", "cmo"]
    delegated.conversation_continues = True
    delegated.delegation_id = "d-1"
    delegated.delegation_status = "queued"
    update = _update(1, 111, "operate vinted")
    update["message"]["from"] = {"id": 222}
    engine = FakeEngine(tmp_path, script=[delegated])
    transport = FakeTransport([[update]])
    worker = TelegramWorker(engine, transport=transport)
    worker.poll_once()
    worker.handle_credential_action_event({
        "delegation_id": "d-1",
        "task_id": "t-1",
        "action": {
            "id": "a-1",
            "kind": "reauth",
            "reason": "Session expired",
        },
    })
    edit = [
        params for method, params in transport.calls
        if method == "editMessageText"
    ][-1]
    retry = edit["reply_markup"]["inline_keyboard"][-1][0]

    outcome = worker.handle_update({
        "update_id": 2,
        "callback_query": {
            "id": "cb-credential",
            "from": {"id": 333},
            "data": retry["callback_data"],
            "message": {
                "message_id": 1,
                "chat": {"id": 111},
            },
        },
    })

    assert outcome["reason"] == "delegation_context_mismatch"
    assert engine.resumed_credential_tasks == []


def test_delegation_status_exposes_working_cancel_control(tmp_path):
    delegated = _result("delegated", "Background review started.", "s-delegated")
    delegated.project_id = "vinted"
    delegated.active_agent_id = "ceo"
    delegated.agents_used = ["ceo", "cmo"]
    delegated.conversation_continues = True
    delegated.delegation_id = "d-1"
    delegated.delegation_status = "queued"
    engine = FakeEngine(
        tmp_path,
        script=[delegated],
    )
    transport = FakeTransport([[_update(1, 111, "review campaign")]])
    worker = TelegramWorker(engine, transport=transport)
    worker.poll_once()

    initial = transport.sent[0]
    cancel_button = initial["reply_markup"]["inline_keyboard"][0][0]
    buttons = [
        button
        for row in initial["reply_markup"]["inline_keyboard"]
        for button in row
    ]
    outcome = worker.handle_update({
        "update_id": 2,
        "callback_query": {
            "id": "callback-1",
            "data": cancel_button["callback_data"],
            "from": {"id": 222},
            "message": {"chat": {"id": 111}, "message_id": 1},
        },
    })

    assert cancel_button["text"] == "Cancel"
    assert {button["text"] for button in buttons} == {
        "Refresh",
        "Details",
        "Cancel",
    }
    assert engine.cancelled_delegations == ["d-1"]
    assert outcome["status"] == "cancelled"
    assert any(
        method == "answerCallbackQuery"
        for method, _params in transport.calls
    )


def test_delegation_cancel_rejects_callback_from_another_chat(tmp_path):
    delegated = _result("delegated", "Background review started.", "s-delegated")
    delegated.project_id = "vinted"
    delegated.active_agent_id = "ceo"
    delegated.agents_used = ["ceo", "cmo"]
    delegated.conversation_continues = True
    delegated.delegation_id = "d-1"
    delegated.delegation_status = "queued"
    engine = FakeEngine(
        tmp_path,
        script=[delegated],
        allowed="111,222",
    )
    transport = FakeTransport([[_update(1, 111, "review campaign")]])
    worker = TelegramWorker(engine, transport=transport)
    worker.poll_once()

    outcome = worker.handle_update({
        "update_id": 2,
        "callback_query": {
            "id": "callback-1",
            "data": "delegation:cancel:d-1",
            "from": {"id": 333},
            "message": {"chat": {"id": 222}, "message_id": 9},
        },
    })

    assert outcome == {
        "status": "ignored",
        "reason": "delegation_context_mismatch",
    }
    assert engine.cancelled_delegations == []


def test_delegation_refresh_and_details_controls_project_current_state(tmp_path):
    delegated = _result("delegated", "Background review started.", "s-delegated")
    delegated.project_id = "vinted"
    delegated.active_agent_id = "ceo"
    delegated.agents_used = ["ceo", "cmo"]
    delegated.conversation_continues = True
    delegated.delegation_id = "d-1"
    delegated.delegation_status = "queued"
    engine = FakeEngine(tmp_path, script=[delegated])
    transport = FakeTransport([[_update(1, 111, "review campaign")]])
    worker = TelegramWorker(engine, transport=transport)
    worker.poll_once()

    for update_id, action in enumerate(("refresh", "details"), start=2):
        outcome = worker.handle_update({
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "data": f"delegation:{action}:d-1",
                "from": {"id": 222},
                "message": {"chat": {"id": 111}, "message_id": 1},
            },
        })
        assert outcome["status"] == "active"

    assert any(
        method == "editMessageText"
        and "Tasks: 1/1" in params["text"]
        for method, params in transport.calls
    )
    assert "CMO: completed" in transport.sent[-1]["text"]
    assert "Cost: $0.02" in transport.sent[-1]["text"]


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


def test_message_passes_telegram_identity_context_to_engine(tmp_path):
    engine = FakeEngine(tmp_path)
    session = engine.channel.create_session()
    engine.channel.update_session_state(session.id, SessionStatus.DISPATCHED)
    engine.script = [_result("completed", "Done.", session.id)]
    update = _update(1, 111, "review the campaign")
    update["message"]["from"] = {"id": 222}
    update["message"]["message_thread_id"] = 333
    transport = FakeTransport([[update]])
    worker = TelegramWorker(engine, transport=transport)

    worker.poll_once()

    context = engine.contexts[0]
    assert context.channel == "telegram"
    assert context.account_id == "default"
    assert context.chat_id == "111"
    assert context.thread_id == "333"
    assert context.sender_id == "222"
    assert context.project_id is None
    assert context.active_agent_id is None
    assert transport.sent[0]["message_thread_id"] == "333"


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


def test_specialist_reply_shows_handoff_header_and_keeps_session(tmp_path):
    engine = FakeEngine(tmp_path)
    session = engine.channel.create_session()
    result = _result("completed", "Campaign reviewed.", session.id)
    result.project_id = "Vinted"
    result.active_agent_id = "cmo"
    result.previous_agent_id = "ceo"
    result.handoff_id = "handoff-1"
    result.conversation_continues = True
    engine.script = [result]
    transport = FakeTransport([[_update(1, 111, "review campaign")]])
    worker = TelegramWorker(engine, transport=transport)

    worker.poll_once()

    assert transport.sent[0]["text"].startswith(
        "CEO → CMO\nCMO · Vinted\n\nCampaign reviewed."
    )
    assert ChannelSessionMapStore(engine.db).get("111") == session.id


def test_threads_in_one_chat_continue_separate_sessions(tmp_path):
    engine = FakeEngine(tmp_path)
    first_session = engine.channel.create_session()
    second_session = engine.channel.create_session()
    engine.channel.update_session_state(
        first_session.id,
        SessionStatus.CLARIFYING,
    )
    engine.channel.update_session_state(
        second_session.id,
        SessionStatus.CLARIFYING,
    )
    engine.script = [
        _result("clarify", "First question", first_session.id),
        _result("clarify", "Second question", second_session.id),
        _result("completed", "First done", first_session.id),
    ]
    first = _update(1, 111, "start first")
    first["message"].update(
        {"from": {"id": 222}, "message_thread_id": 10}
    )
    second = _update(2, 111, "start second")
    second["message"].update(
        {"from": {"id": 222}, "message_thread_id": 20}
    )
    first_reply = _update(3, 111, "answer first")
    first_reply["message"].update(
        {"from": {"id": 222}, "message_thread_id": 10}
    )
    worker = TelegramWorker(
        engine,
        transport=FakeTransport([[first], [second], [first_reply]]),
    )

    worker.poll_once()
    worker.poll_once()
    worker.poll_once()

    assert engine.directive_calls == [
        ("start first", None),
        ("start second", None),
        ("answer first", first_session.id),
    ]


def test_sender_aware_key_migrates_legacy_chat_mapping(tmp_path):
    engine = FakeEngine(tmp_path)
    session = engine.channel.create_session()
    engine.channel.update_session_state(
        session.id,
        SessionStatus.CLARIFYING,
    )
    ChannelSessionMapStore(engine.db).set("111", session.id)
    engine.script = [_result("completed", "Done.", session.id)]
    update = _update(1, 111, "the cli tool")
    update["message"]["from"] = {"id": 222}
    worker = TelegramWorker(
        engine,
        transport=FakeTransport([[update]]),
    )

    worker.poll_once()

    assert engine.directive_calls == [("the cli tool", session.id)]
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


def test_failed_go_keeps_retryable_gated_session_mapped(tmp_path):
    engine = FakeEngine(tmp_path)
    session = engine.channel.create_session()
    engine.script = [
        _result("gated", "Preview.", session.id),
        _result("failed", "Execution failed; retry GO.", session.id),
    ]
    transport = FakeTransport(
        [[_update(1, 111, "build it")], [_update(2, 111, "GO")]]
    )
    worker = TelegramWorker(engine, transport=transport)

    worker.poll_once()
    engine.channel.update_session_state(session.id, SessionStatus.GATED)
    worker.poll_once()

    assert ChannelSessionMapStore(engine.db).get("111") == session.id


def test_freeform_message_does_not_continue_gated_session(tmp_path):
    engine = FakeEngine(tmp_path)
    gated = engine.channel.create_session()
    engine.channel.update_session_state(gated.id, SessionStatus.GATED)
    ChannelSessionMapStore(engine.db).set("111", gated.id)
    engine.script = [_result("completed", "Fresh.", "new-session")]
    worker = TelegramWorker(
        engine, transport=FakeTransport([[_update(1, 111, "do something else")]])
    )

    worker.poll_once()

    assert engine.directive_calls == [("do something else", None)]


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
