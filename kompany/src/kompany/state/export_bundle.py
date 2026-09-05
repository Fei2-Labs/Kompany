"""Portable encrypted company bundle: export / import / handoff marker.

A bundle is the full engine state as one passphrase-encrypted file:
the SQLite database (snapshotted live via ``Connection.backup()``),
``config.yaml``, the vault master key, and any ``*.key`` files at the
data_dir root (e.g. a git-crypt key). Importing the bundle on a fresh
machine fully reconstitutes the company.

File format (version 1)::

    KOMPANYBUNDLE1\n
    <one-line JSON header: version, kdf, iterations, salt, created_at, files>\n
    <Fernet token of a gzipped tar archive of the files>

Secrets never leave the machine in plaintext: the tar payload is
encrypted with a Fernet key derived from the passphrase (PBKDF2-HMAC-
SHA256). The header is metadata only.

The ``--handoff`` flow additionally writes ``<data_dir>/.exported.json``
— a tombstone telling this machine's engine/daemon that the company
moved elsewhere, so two machines never tick the same company.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"KOMPANYBUNDLE1\n"
KDF_ITERATIONS = 600_000
EXPORTED_MARKER = ".exported.json"
DB_FILENAME = "kompany.db"
SECRET_FILENAMES = {".vault-master.key"}


class BundlePassphraseError(ValueError):
    """Wrong passphrase (or corrupted bundle payload)."""


def derive_key(passphrase: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> bytes:
    """Derive a Fernet key from a passphrase via PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _bundle_files(data_dir: Path) -> list[Path]:
    """Config + secret key files at the data_dir root (DB handled separately)."""
    files: list[Path] = []
    config = data_dir / "config.yaml"
    if config.is_file():
        files.append(config)
    for path in sorted(data_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name in SECRET_FILENAMES or path.suffix == ".key":
            files.append(path)
    return files


EXTENSIONS_DIR = "extensions"


def _extension_members(data_dir: Path) -> list[tuple[str, Path]]:
    """Customer-evolution layer (07-24): every file under ``extensions/``
    travels with the company, independent of vendor release state."""
    root = data_dir / EXTENSIONS_DIR
    if not root.is_dir():
        return []
    return [(f"{EXTENSIONS_DIR}/{p.relative_to(root).as_posix()}", p)
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]


def _safe_extension_member(name: str) -> Path | None:
    """``extensions/<rel>`` with no traversal → relative Path; else None."""
    if not name.startswith(EXTENSIONS_DIR + "/"):
        return None
    rel = Path(name)
    if rel.is_absolute() or any(part in ("..", "") for part in rel.parts):
        return None
    return rel


def _snapshot_db(data_dir: Path, target: Path) -> None:
    """Snapshot the live database (WAL-safe) to ``target``."""
    db_path = data_dir / DB_FILENAME
    if not db_path.exists():
        target.touch()
        return
    src = sqlite3.connect(str(db_path))
    try:
        dest = sqlite3.connect(str(target))
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()


def create_bundle(
    data_dir: Path,
    passphrase: str,
    out_path: Path | None = None,
) -> dict:
    """Export the full engine state to a passphrase-encrypted bundle file.

    Returns metadata: ``{path, size_bytes, files, created_at}``.
    """
    if not passphrase:
        raise ValueError("A non-empty passphrase is required")
    data_dir = Path(data_dir).expanduser()
    created_at = datetime.now(UTC)
    if out_path is None:
        ts = created_at.strftime("%Y%m%dT%H%M%S")
        out_path = Path.cwd() / f"kompany-export-{ts}.kmp"
    out_path = Path(out_path).expanduser()

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / DB_FILENAME
        _snapshot_db(data_dir, snapshot)
        members = [(DB_FILENAME, snapshot)]
        members += [(p.name, p) for p in _bundle_files(data_dir)]
        members += _extension_members(data_dir)

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, path in members:
                tar.add(str(path), arcname=name)
        payload = buf.getvalue()

    salt = os.urandom(16)
    token = Fernet(derive_key(passphrase, salt)).encrypt(payload)
    header = {
        "version": 1,
        "kdf": "pbkdf2-sha256",
        "iterations": KDF_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "created_at": created_at.isoformat(),
        "files": [name for name, _ in members],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(MAGIC)
        f.write(json.dumps(header).encode("utf-8") + b"\n")
        f.write(token)
    return {
        "path": str(out_path),
        "size_bytes": out_path.stat().st_size,
        "files": header["files"],
        "created_at": header["created_at"],
    }


def read_bundle_header(bundle_path: Path) -> dict:
    """Return the plaintext metadata header of a bundle file."""
    with open(Path(bundle_path).expanduser(), "rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"Not a Kompany bundle: {bundle_path}")
        return json.loads(f.readline().decode("utf-8"))


def import_bundle(
    bundle_path: Path,
    passphrase: str,
    data_dir: Path,
    force: bool = False,
) -> dict:
    """Reconstitute the company state from a bundle into ``data_dir``.

    Refuses to overwrite an existing database unless ``force``. Wrong
    passphrase raises :class:`BundlePassphraseError`. Clears any
    exported tombstone so the imported company is live here.
    """
    bundle_path = Path(bundle_path).expanduser()
    data_dir = Path(data_dir).expanduser()
    db_path = data_dir / DB_FILENAME
    if db_path.exists() and not force:
        raise FileExistsError(
            f"A company database already exists at {db_path}. "
            "Re-run with --force to overwrite it."
        )

    with open(bundle_path, "rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"Not a Kompany bundle: {bundle_path}")
        header = json.loads(f.readline().decode("utf-8"))
        token = f.read()

    salt = base64.b64decode(header["salt"])
    iterations = int(header.get("iterations", KDF_ITERATIONS))
    try:
        payload = Fernet(derive_key(passphrase, salt, iterations)).decrypt(token)
    except InvalidToken as exc:
        raise BundlePassphraseError(
            "Could not decrypt bundle: wrong passphrase or corrupted file"
        ) from exc

    data_dir.mkdir(parents=True, exist_ok=True)
    # Stale WAL/SHM from a previous db must not shadow the imported snapshot.
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()

    written: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            ext_rel = _safe_extension_member(name) if member.isfile() else None
            if ext_rel is None and (not member.isfile() or "/" in name or "\\" in name or name.startswith(".")):
                if name not in SECRET_FILENAMES:
                    continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            if ext_rel is not None:
                target = data_dir / ext_rel
                target.parent.mkdir(parents=True, exist_ok=True)
            else:
                target = data_dir / Path(name).name
            secret = target.suffix == ".key" or target.name in SECRET_FILENAMES
            fd = os.open(
                str(target),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600 if secret else 0o644,
            )
            try:
                os.write(fd, extracted.read())
            finally:
                os.close(fd)
            written.append(str(ext_rel) if ext_rel is not None else target.name)

    marker = data_dir / EXPORTED_MARKER
    if marker.exists():
        marker.unlink()
    return {
        "data_dir": str(data_dir),
        "files": written,
        "bundle_created_at": header.get("created_at"),
        "imported_at": datetime.now(UTC).isoformat(),
    }


def exported_marker_path(data_dir: Path) -> Path:
    return Path(data_dir).expanduser() / EXPORTED_MARKER


def write_exported_marker(data_dir: Path, bundle_path: str) -> dict:
    """Tombstone this data_dir: the company was handed off elsewhere."""
    meta = {
        "exported_at": datetime.now(UTC).isoformat(),
        "bundle_path": bundle_path,
    }
    exported_marker_path(data_dir).write_text(json.dumps(meta, indent=2))
    return meta


def read_exported_marker(data_dir: Path) -> dict | None:
    """Return the tombstone metadata, or None when this data_dir is live."""
    path = exported_marker_path(data_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"exported_at": None, "bundle_path": None}
