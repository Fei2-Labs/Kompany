"""Authenticated inbound remote command models and parsing."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RemoteCommandRequest(BaseModel):
    source: str = "mobile"  # "telegram" | "mobile"
    text: str = ""
    chat_id: str = ""
    account_id: str = "default"
    thread_id: str | None = None
    sender_id: str = ""
    bearer_token: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class RemoteCommandResult(BaseModel):
    source: str
    status: str  # "denied" | "executed" | "unknown_command"
    command: str = ""
    message: str = ""
    result: dict[str, Any] | list[dict[str, Any]] | None = None
    replayed: bool = False


def parse_remote_text(text: str) -> tuple[str, list[str]]:
    parts = text.strip().split()
    if not parts:
        return "help", []
    command = parts[0].removeprefix("/").lower()
    return command, parts[1:]


def request_from_telegram_update(update: dict[str, Any]) -> RemoteCommandRequest:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    text = message.get("text") or ""
    return RemoteCommandRequest(
        source="telegram",
        text=text,
        chat_id=str(chat.get("id", "")),
        account_id="default",
        thread_id=(
            str(message["message_thread_id"])
            if message.get("message_thread_id") is not None
            else None
        ),
        sender_id=str(sender.get("id", "")),
        payload={"update_id": update.get("update_id")},
    )
