"""Vault master key resolution helpers."""

from __future__ import annotations

import importlib
from typing import Any

from cryptography.fernet import Fernet

DEFAULT_VAULT_KEYCHAIN_ACCOUNT = "vault-master-key"
DEFAULT_VAULT_KEYCHAIN_SERVICE = "kompany"


def resolve_vault_key(
    env_key: str,
    keychain_service: str = DEFAULT_VAULT_KEYCHAIN_SERVICE,
    keychain_account: str = DEFAULT_VAULT_KEYCHAIN_ACCOUNT,
) -> tuple[str, str]:
    keychain_key = get_vault_key_from_keychain(
        service=keychain_service,
        account=keychain_account,
    )
    if keychain_key:
        return keychain_key, "keychain"

    if env_key:
        if _is_valid_fernet_key(env_key):
            set_vault_key_in_keychain(
                env_key,
                service=keychain_service,
                account=keychain_account,
            )
            return env_key, "keychain"
        return env_key, "env"

    return "", "missing"


def get_vault_key_from_keychain(
    service: str,
    account: str,
) -> str | None:
    keyring = _load_keyring()
    if keyring is None:
        return None
    try:
        return keyring.get_password(service, account)
    except Exception:
        return None


def set_vault_key_in_keychain(
    vault_key: str,
    service: str,
    account: str,
) -> bool:
    keyring = _load_keyring()
    if keyring is None:
        return False
    try:
        keyring.set_password(service, account, vault_key)
        return True
    except Exception:
        return False


def _load_keyring() -> Any | None:
    try:
        return importlib.import_module("keyring")
    except ImportError:
        return None


def _is_valid_fernet_key(vault_key: str) -> bool:
    try:
        Fernet(vault_key.encode("utf-8"))
    except Exception:
        return False
    return True
