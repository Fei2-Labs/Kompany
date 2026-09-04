"""Encrypted credential vault backed by SQLite."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel

from kompany.state.database import Database

ALLOWED_CREDENTIALS = {
    "anthropic_api_key",
    "openai_api_key",
    "gemini_api_key",
    "glm_api_key",
    "kimi_api_key",
    "custom_api_key",
    "custom_base_url",
    "telegram_bot_token",
    "telegram_chat_id",
    "telegram_allowed_chat_ids",
    "mobile_remote_token",
    "web_dashboard_token",
    # Email integration (#5) — generic SMTP so it works with Gmail
    # app-passwords or any SMTP provider. Lets agents actually SEND
    # outreach instead of only drafting it.
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_from",
    # Resend (native API) — preferred over raw SMTP when present.
    "resend_api_key",
    "resend_from",
    # OAuth-subscription credentials (06-16-agentic-chat-engine P3).
    # The OpenAI Codex/ChatGPT PKCE token sink — a JSON blob holding
    # {access_token, refresh_token, expires_at, account_id}. Encrypted at
    # rest like every other vault row; never written to settings/env.
    "openai_oauth_tokens",
    # Pro integrations (Fei2-Labs/kompany-pro) — declared via each
    # integration's `required_credentials`. Core owns the vault
    # allowlist since it's the single security boundary for what may be
    # stored/read, even though the integration itself lives in Pro.
    "stripe_api_key",
}


class CredentialVaultError(RuntimeError):
    pass


class CredentialVaultEntry(BaseModel):
    name: str
    configured: bool = True
    updated_at: str | None = None


class CredentialVaultStore:
    """Encrypts credential values before writing them to SQLite."""

    def __init__(self, db: Database, vault_key: str = ""):
        self.db = db
        self.vault_key = vault_key

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("utf-8")

    def list(self) -> list[CredentialVaultEntry]:
        # Return the FULL catalog of supported credentials, marking each
        # as configured=True when a row exists in the vault (with its
        # updated_at) and configured=False when it's allowed but not yet
        # stored. This lets the settings UI render an "add credential"
        # control for names the founder hasn't seeded yet (e.g. when
        # custom_api_key currently lives only in the systemd unit) without
        # a second endpoint — callers that only care about configured
        # rows filter on configured=True.
        rows = self.db.execute(
            """SELECT name, updated_at FROM credential_vault
               ORDER BY name"""
        ).fetchall()
        stored = {row["name"]: row["updated_at"] for row in rows}
        entries: list[CredentialVaultEntry] = []
        for name in sorted(ALLOWED_CREDENTIALS):
            if name in stored:
                entries.append(
                    CredentialVaultEntry(name=name, configured=True, updated_at=stored[name])
                )
            else:
                entries.append(CredentialVaultEntry(name=name, configured=False))
        return entries

    def set(self, name: str, value: str) -> CredentialVaultEntry:
        self._validate_name(name)
        ciphertext = self._fernet().encrypt(value.encode("utf-8")).decode("utf-8")
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            """INSERT INTO credential_vault (name, ciphertext, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 ciphertext = excluded.ciphertext,
                 updated_at = excluded.updated_at""",
            (name, ciphertext, now),
        )
        self.db.commit()
        return CredentialVaultEntry(name=name, updated_at=now)

    def get(self, name: str) -> str | None:
        self._validate_name(name)
        row = self.db.execute(
            """SELECT ciphertext FROM credential_vault
               WHERE name = ?""",
            (name,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._fernet().decrypt(row["ciphertext"].encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise CredentialVaultError("credential vault key cannot decrypt stored value") from exc

    def delete(self, name: str) -> dict[str, Any]:
        self._validate_name(name)
        cur = self.db.execute(
            "DELETE FROM credential_vault WHERE name = ?",
            (name,),
        )
        self.db.commit()
        return {"name": name, "deleted": cur.rowcount > 0}

    def rotate_key(self, new_vault_key: str) -> dict[str, Any]:
        old_fernet = self._fernet()
        new_fernet = self._fernet_for_key(new_vault_key)
        rows = self.db.execute(
            """SELECT name, ciphertext FROM credential_vault
               ORDER BY name"""
        ).fetchall()
        rotated = []
        try:
            for row in rows:
                plaintext = old_fernet.decrypt(row["ciphertext"].encode("utf-8"))
                rotated.append((
                    row["name"],
                    new_fernet.encrypt(plaintext).decode("utf-8"),
                ))
        except InvalidToken as exc:
            raise CredentialVaultError("credential vault key cannot decrypt stored value") from exc

        for name, ciphertext in rotated:
            self.db.execute(
                """UPDATE credential_vault
                   SET ciphertext = ?, updated_at = ?
                   WHERE name = ?""",
                (ciphertext, datetime.now(UTC).isoformat(), name),
            )
        self.db.commit()
        self.vault_key = new_vault_key
        return {"rotated": len(rotated), "names": [name for name, _ in rotated]}

    def _fernet(self) -> Fernet:
        return self._fernet_for_key(self.vault_key)

    @staticmethod
    def _fernet_for_key(vault_key: str) -> Fernet:
        if not vault_key:
            raise CredentialVaultError("KOMPANY_VAULT_KEY is required for credential vault access")
        try:
            return Fernet(vault_key.encode("utf-8"))
        except ValueError as exc:
            raise CredentialVaultError("KOMPANY_VAULT_KEY is invalid") from exc

    @staticmethod
    def _validate_name(name: str) -> None:
        if name not in ALLOWED_CREDENTIALS:
            raise CredentialVaultError(f"credential '{name}' is not supported")
