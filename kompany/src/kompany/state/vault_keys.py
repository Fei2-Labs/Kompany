"""Vault master key resolution helpers.

Resolution order (highest priority first):

1. ``env_key`` — when the caller passed an explicit key (e.g. unit
   tests, ``KOMPANY_VAULT_KEY`` env override). Persisted to file +
   keychain as a side effect so the next boot finds it cheaply.
2. ``<data_dir>/.vault-master.key`` (chmod 0600) — file-based store
   that works without OS gates. Default for desktop / CLI installs
   because the ad-hoc-signed sidecar binary changes identity every
   rebuild, which makes macOS keychain "Always Allow" not stick.
3. macOS Keychain (``service=kompany``, ``account=vault-master-key``)
   — kept as a fallback so existing installs that already populated
   it continue to work, and as the opt-in store for signed/notarized
   release builds.
4. Generate a fresh Fernet key when nothing exists, write to file
   (and best-effort keychain), and return it.

The vault key is the symmetric key used by ``CredentialVaultStore``
to encrypt rows in ``credential_vault``. The encrypted blobs already
live in ``<data_dir>/kompany.db``; keeping the key in a sibling file
inside the same user-owned dir does not weaken the threat model
versus an attacker with arbitrary read on ``<data_dir>``: they
already have the ciphertext too.
"""

from __future__ import annotations

import importlib
import os
import stat
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

DEFAULT_VAULT_KEYCHAIN_ACCOUNT = "vault-master-key"
DEFAULT_VAULT_KEYCHAIN_SERVICE = "kompany"
VAULT_KEY_FILENAME = ".vault-master.key"


def resolve_vault_key(
    env_key: str,
    keychain_service: str = DEFAULT_VAULT_KEYCHAIN_SERVICE,
    keychain_account: str = DEFAULT_VAULT_KEYCHAIN_ACCOUNT,
    data_dir: Path | str | None = None,
) -> tuple[str, str]:
    # 1. Explicit env override — persist + return.
    if env_key:
        valid = _is_valid_fernet_key(env_key)
        source = "env"
        if valid:
            if data_dir is not None:
                _write_vault_key_to_file(env_key, Path(data_dir))
            set_vault_key_in_keychain(
                env_key,
                service=keychain_service,
                account=keychain_account,
            )
            source = "env (persisted to file + keychain)"
        return env_key, source

    # 2. File-based store (preferred default — no OS prompt).
    if data_dir is not None:
        file_key = _read_vault_key_from_file(Path(data_dir))
        if file_key:
            return file_key, "file"

    # 3. Keychain fallback (existing installs).
    keychain_key = get_vault_key_from_keychain(
        service=keychain_service,
        account=keychain_account,
    )
    if keychain_key:
        # Migrate to file so subsequent boots skip the keychain prompt
        # (ad-hoc-signed sidecars don't pin "Always Allow" reliably).
        if data_dir is not None:
            _write_vault_key_to_file(keychain_key, Path(data_dir))
        return keychain_key, "keychain"

    # 4. Generate a fresh key, write file, best-effort keychain.
    if data_dir is not None:
        fresh = Fernet.generate_key().decode("utf-8")
        _write_vault_key_to_file(fresh, Path(data_dir))
        set_vault_key_in_keychain(
            fresh,
            service=keychain_service,
            account=keychain_account,
        )
        return fresh, "generated"

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


def _vault_key_path(data_dir: Path) -> Path:
    return Path(data_dir).expanduser() / VAULT_KEY_FILENAME


def _read_vault_key_from_file(data_dir: Path) -> str | None:
    path = _vault_key_path(data_dir)
    try:
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8").strip()
        return raw or None
    except OSError:
        return None


def _write_vault_key_to_file(vault_key: str, data_dir: Path) -> bool:
    """Persist the key to ``<data_dir>/.vault-master.key`` with 0600
    permissions. Best-effort: returns False on filesystem errors but
    never raises (the caller's failure mode is "re-prompt next boot",
    which is recoverable)."""
    try:
        data_dir = Path(data_dir).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        path = _vault_key_path(data_dir)
        fd = os.open(
            str(path),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            os.write(fd, vault_key.encode("utf-8"))
        finally:
            os.close(fd)
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return True
    except OSError:
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
