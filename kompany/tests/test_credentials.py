from __future__ import annotations

import pytest

from kompany.core.engine import KompanyEngine
from kompany.state.credentials import CredentialVaultError, CredentialVaultStore
from kompany.state.database import Database
from kompany.state.vault_keys import resolve_vault_key


def test_credential_vault_encrypts_values_at_rest(tmp_path):
    key = CredentialVaultStore.generate_key()
    db = Database(tmp_path)
    store = CredentialVaultStore(db, key)

    entry = store.set("telegram_bot_token", "secret-token")
    row = db.execute(
        "SELECT ciphertext FROM credential_vault WHERE name = ?",
        ("telegram_bot_token",),
    ).fetchone()

    assert entry.name == "telegram_bot_token"
    assert store.get("telegram_bot_token") == "secret-token"
    assert "secret-token" not in row["ciphertext"]


def test_credential_vault_requires_key_for_access(tmp_path):
    db = Database(tmp_path)
    store = CredentialVaultStore(db, "")

    with pytest.raises(CredentialVaultError, match="KOMPANY_VAULT_KEY"):
        store.set("mobile_remote_token", "secret")


def test_credential_vault_rejects_unknown_names(tmp_path):
    db = Database(tmp_path)
    store = CredentialVaultStore(db, CredentialVaultStore.generate_key())

    with pytest.raises(CredentialVaultError, match="not supported"):
        store.set("unknown_secret", "secret")


def test_credential_vault_rotates_key_and_preserves_values(tmp_path):
    old_key = CredentialVaultStore.generate_key()
    new_key = CredentialVaultStore.generate_key()
    db = Database(tmp_path)
    store = CredentialVaultStore(db, old_key)
    store.set("mobile_remote_token", "mobile-secret")
    old_ciphertext = db.execute(
        "SELECT ciphertext FROM credential_vault WHERE name = ?",
        ("mobile_remote_token",),
    ).fetchone()["ciphertext"]

    result = store.rotate_key(new_key)
    new_ciphertext = db.execute(
        "SELECT ciphertext FROM credential_vault WHERE name = ?",
        ("mobile_remote_token",),
    ).fetchone()["ciphertext"]

    assert result == {"rotated": 1, "names": ["mobile_remote_token"]}
    assert new_ciphertext != old_ciphertext
    assert store.get("mobile_remote_token") == "mobile-secret"
    with pytest.raises(CredentialVaultError, match="cannot decrypt"):
        CredentialVaultStore(db, old_key).get("mobile_remote_token")
    assert CredentialVaultStore(db, new_key).get("mobile_remote_token") == "mobile-secret"


def test_credential_vault_rotation_rejects_invalid_new_key_without_rewrite(tmp_path):
    old_key = CredentialVaultStore.generate_key()
    db = Database(tmp_path)
    store = CredentialVaultStore(db, old_key)
    store.set("mobile_remote_token", "mobile-secret")
    old_ciphertext = db.execute(
        "SELECT ciphertext FROM credential_vault WHERE name = ?",
        ("mobile_remote_token",),
    ).fetchone()["ciphertext"]

    with pytest.raises(CredentialVaultError, match="invalid"):
        store.rotate_key("not-a-fernet-key")

    current_ciphertext = db.execute(
        "SELECT ciphertext FROM credential_vault WHERE name = ?",
        ("mobile_remote_token",),
    ).fetchone()["ciphertext"]
    assert current_ciphertext == old_ciphertext
    assert store.get("mobile_remote_token") == "mobile-secret"


def test_credential_vault_rotation_wrong_current_key_leaves_rows_unchanged(tmp_path):
    old_key = CredentialVaultStore.generate_key()
    wrong_key = CredentialVaultStore.generate_key()
    new_key = CredentialVaultStore.generate_key()
    db = Database(tmp_path)
    CredentialVaultStore(db, old_key).set("mobile_remote_token", "mobile-secret")
    old_ciphertext = db.execute(
        "SELECT ciphertext FROM credential_vault WHERE name = ?",
        ("mobile_remote_token",),
    ).fetchone()["ciphertext"]

    with pytest.raises(CredentialVaultError, match="cannot decrypt"):
        CredentialVaultStore(db, wrong_key).rotate_key(new_key)

    current_ciphertext = db.execute(
        "SELECT ciphertext FROM credential_vault WHERE name = ?",
        ("mobile_remote_token",),
    ).fetchone()["ciphertext"]
    assert current_ciphertext == old_ciphertext
    assert CredentialVaultStore(db, old_key).get("mobile_remote_token") == "mobile-secret"


def test_engine_applies_vault_credentials_when_env_absent(tmp_path, monkeypatch):
    key = CredentialVaultStore.generate_key()
    db = Database(tmp_path)
    CredentialVaultStore(db, key).set("mobile_remote_token", "vault-mobile-token")
    db.close()
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KOMPANY_VAULT_KEY", key)
    monkeypatch.delenv("MOBILE_REMOTE_TOKEN", raising=False)

    engine = KompanyEngine()

    assert engine.settings.mobile_remote_token == "vault-mobile-token"


def test_env_credentials_override_vault_credentials(tmp_path, monkeypatch):
    key = CredentialVaultStore.generate_key()
    db = Database(tmp_path)
    CredentialVaultStore(db, key).set("mobile_remote_token", "vault-mobile-token")
    db.close()
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KOMPANY_VAULT_KEY", key)
    monkeypatch.setenv("MOBILE_REMOTE_TOKEN", "env-mobile-token")

    engine = KompanyEngine()

    assert engine.settings.mobile_remote_token == "env-mobile-token"


def test_resolve_vault_key_prefers_keychain(monkeypatch):
    monkeypatch.setattr(
        "kompany.state.vault_keys.get_vault_key_from_keychain",
        lambda service, account: "keychain-key",
    )

    resolved, source = resolve_vault_key("env-key")

    assert resolved == "keychain-key"
    assert source == "keychain"


def test_resolve_vault_key_falls_back_to_env_without_keychain(monkeypatch):
    monkeypatch.setattr(
        "kompany.state.vault_keys.get_vault_key_from_keychain",
        lambda service, account: None,
    )

    resolved, source = resolve_vault_key("env-key")

    assert resolved == "env-key"
    assert source == "env"

