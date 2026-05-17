"""Tests for notification adapters."""

from __future__ import annotations

import json

from kompany.notifications import DryRunNotifier, TelegramNotifier


def _event():
    return {
        "kind": "pending_approvals",
        "severity": "action_required",
        "summary": "1 approval request waiting.",
        "payload": {"approval_ids": ["app-1"]},
    }


def test_dry_run_notifier_returns_delivery():
    delivery = DryRunNotifier().send(_event())

    assert delivery.status == "dry_run"
    assert delivery.adapter == "dry-run"
    assert delivery.kind == "pending_approvals"


def test_telegram_notifier_skips_without_credentials():
    delivery = TelegramNotifier(bot_token="", chat_id="").send(_event())

    assert delivery.status == "skipped"
    assert delivery.error == "telegram credentials not configured"


def test_telegram_notifier_posts_send_message(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true, "result": {"message_id": 42}}'

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("kompany.notifications.request.urlopen", fake_urlopen)

    delivery = TelegramNotifier(bot_token="secret-token", chat_id="chat-1").send(_event())

    assert delivery.status == "sent"
    assert delivery.provider_message_id == "42"
    assert captured["url"].endswith("/sendMessage")
    assert "secret-token" in captured["url"]
    assert captured["data"]["chat_id"] == "chat-1"
    assert "1 approval request waiting." in captured["data"]["text"]


def test_telegram_notifier_failure_hides_token(monkeypatch):
    def fake_urlopen(req, timeout=10):
        raise RuntimeError(f"failed {req.full_url}")

    monkeypatch.setattr("kompany.notifications.request.urlopen", fake_urlopen)

    delivery = TelegramNotifier(bot_token="secret-token", chat_id="chat-1").send(_event())

    assert delivery.status == "failed"
    assert "secret-token" not in delivery.error
