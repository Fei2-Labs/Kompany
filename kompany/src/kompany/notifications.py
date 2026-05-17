"""Notification delivery adapters for heartbeat events."""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib import request

from pydantic import BaseModel, Field


class NotificationDelivery(BaseModel):
    adapter: str
    status: str
    kind: str
    summary: str
    destination: str = ""
    error: str | None = None
    provider_message_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Notifier(Protocol):
    adapter: str

    def send(self, event: dict[str, Any]) -> NotificationDelivery:
        ...


class DryRunNotifier:
    adapter = "dry-run"

    def send(self, event: dict[str, Any]) -> NotificationDelivery:
        return NotificationDelivery(
            adapter=self.adapter,
            status="dry_run",
            kind=event["kind"],
            summary=event["summary"],
            payload={"severity": event.get("severity", "info")},
        )


class TelegramNotifier:
    adapter = "telegram"

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, event: dict[str, Any]) -> NotificationDelivery:
        if not self.bot_token or not self.chat_id:
            return NotificationDelivery(
                adapter=self.adapter,
                status="skipped",
                kind=event["kind"],
                summary=event["summary"],
                destination="telegram",
                error="telegram credentials not configured",
            )

        data = json.dumps({
            "chat_id": self.chat_id,
            "text": self._format(event),
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return NotificationDelivery(
                adapter=self.adapter,
                status="failed",
                kind=event["kind"],
                summary=event["summary"],
                destination=self.chat_id,
                error=f"{type(exc).__name__}: request failed",
            )

        message = body.get("result", {}) if isinstance(body, dict) else {}
        return NotificationDelivery(
            adapter=self.adapter,
            status="sent",
            kind=event["kind"],
            summary=event["summary"],
            destination=self.chat_id,
            provider_message_id=str(message.get("message_id", "")) or None,
        )

    def _format(self, event: dict[str, Any]) -> str:
        severity = event.get("severity", "info")
        return f"Kompany [{severity}] {event['summary']}"


def build_notifier(settings: Any, adapter: str = "dry-run") -> Notifier:
    if adapter == "telegram":
        return TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
    return DryRunNotifier()
