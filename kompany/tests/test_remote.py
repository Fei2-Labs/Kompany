from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kompany.remote import parse_remote_text, request_from_telegram_update
from kompany.state.database import Database
from kompany.state.remote_replay import RemoteReplayStore


def test_parse_remote_text_normalizes_slash_command():
    command, args = parse_remote_text("/approve app-1")

    assert command == "approve"
    assert args == ["app-1"]


def test_parse_remote_text_empty_defaults_to_help():
    command, args = parse_remote_text("   ")

    assert command == "help"
    assert args == []


def test_request_from_telegram_update_extracts_message_context():
    request = request_from_telegram_update({
        "update_id": 123,
        "message": {
            "chat": {"id": 456},
            "text": "/status",
        },
    })

    assert request.source == "telegram"
    assert request.text == "/status"
    assert request.chat_id == "456"
    assert request.payload == {"update_id": 123}


def test_request_from_telegram_update_accepts_edited_message():
    request = request_from_telegram_update({
        "update_id": 124,
        "edited_message": {
            "chat": {"id": "789"},
            "text": "heartbeat",
        },
    })

    assert request.source == "telegram"
    assert request.text == "heartbeat"
    assert request.chat_id == "789"


def test_remote_replay_store_persists_result_by_source_and_key(tmp_path):
    db = Database(tmp_path)
    store = RemoteReplayStore(db)
    result = {
        "source": "mobile",
        "status": "executed",
        "command": "approve",
        "message": "Approval updated",
        "result": {"id": "app-1", "status": "approved"},
        "replayed": False,
    }

    store.store("mobile", "nonce-1", "approve", result)
    restored = store.get("mobile", "nonce-1")

    assert restored == result
    assert store.get("telegram", "nonce-1") is None


def test_remote_replay_store_cleanup_deletes_only_expired_rows(tmp_path):
    db = Database(tmp_path)
    store = RemoteReplayStore(db)
    result = {
        "source": "mobile",
        "status": "executed",
        "command": "status",
        "message": "ok",
        "result": {"ok": True},
        "replayed": False,
    }
    store.store("mobile", "old-nonce", "status", result)
    store.store("mobile", "fresh-nonce", "status", result)
    old_created_at = (datetime.now(UTC) - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "UPDATE remote_command_replays SET created_at = ? WHERE replay_key = ?",
        (old_created_at, "old-nonce"),
    )
    db.commit()

    cleanup = store.cleanup(7 * 24 * 60 * 60)

    assert cleanup["deleted"] == 1
    assert cleanup["remaining"] == 1
    assert cleanup["ttl_seconds"] == 7 * 24 * 60 * 60
    assert store.get("mobile", "old-nonce") is None
    assert store.get("mobile", "fresh-nonce") == result


def test_remote_replay_store_cleanup_is_safe_when_empty(tmp_path):
    db = Database(tmp_path)
    store = RemoteReplayStore(db)

    cleanup = store.cleanup(3600)

    assert cleanup["deleted"] == 0
    assert cleanup["remaining"] == 0
    assert cleanup["ttl_seconds"] == 3600
