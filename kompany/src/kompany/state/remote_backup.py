"""S3-compatible remote backup adapter for encrypted export bundles.

Heartbeat periodically produces a passphrase-encrypted export bundle
(same format as ``kompany export``) and uploads it to S3-compatible
storage (Cloudflare R2, Backblaze B2, AWS S3, MinIO, …). Restore =
download + ``kompany import`` — one tested path for both disaster
recovery and machine migration.

Configuration lives in ``config.yaml`` under ``remote_backup:``::

    remote_backup:
      endpoint_url: https://<account>.r2.cloudflarestorage.com
      bucket: kompany-backups
      region: auto
      access_key_id: ...
      secret_access_key: ...
      passphrase: ...          # encrypts bundles (required)
      prefix: kompany/         # key prefix, default "kompany/"
      retain: 7                # keep last N bundles, default 7

Credentials may also be supplied via environment variables
(``KOMPANY_REMOTE_BACKUP_*``) or the credential vault; the config
block is the primary path for the VPS deployment where ``config.yaml``
is the single source of truth.

boto3 is an optional dependency (``pip install kompany[backup]``); the
import is lazy so the engine boots fine without it.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kompany.state.export_bundle import (
    BundlePassphraseError,
    create_bundle,
    import_bundle,
    read_bundle_header,
)


class RemoteBackupError(Exception):
    """Remote backup upload/download/config error."""


class RemoteBackupConfig:
    """Parsed ``remote_backup`` config block."""

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        passphrase: str,
        region: str = "auto",
        prefix: str = "kompany/",
        retain: int = 7,
    ):
        self.endpoint_url = endpoint_url
        self.bucket = bucket
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.passphrase = passphrase
        self.region = region
        self.prefix = prefix
        self.retain = retain

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RemoteBackupConfig":
        missing = [
            f for f in ("endpoint_url", "bucket", "access_key_id",
                        "secret_access_key", "passphrase")
            if not data.get(f)
        ]
        if missing:
            raise RemoteBackupError(
                f"remote_backup config missing required field(s): {', '.join(missing)}"
            )
        return cls(
            endpoint_url=data["endpoint_url"],
            bucket=data["bucket"],
            access_key_id=data["access_key_id"],
            secret_access_key=data["secret_access_key"],
            passphrase=data["passphrase"],
            region=data.get("region", "auto"),
            prefix=data.get("prefix", "kompany/"),
            retain=int(data.get("retain", 7)),
        )

    def is_configured(self) -> bool:
        return bool(self.endpoint_url and self.bucket and self.passphrase)


def _s3_client(cfg: RemoteBackupConfig):
    """Lazy-create a boto3 S3 client."""
    try:
        import boto3
    except ImportError as exc:
        raise RemoteBackupError(
            "boto3 is not installed. Run: pip install 'kompany[backup]'"
        ) from exc
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        region_name=cfg.region,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
    )


def _bundle_key(cfg: RemoteBackupConfig, created_at: datetime) -> str:
    ts = created_at.strftime("%Y%m%dT%H%M%S%f")
    return f"{cfg.prefix}kompany-{ts}.kmp"


def upload_bundle(
    cfg: RemoteBackupConfig,
    data_dir: Path,
    *,
    client=None,
) -> dict:
    """Create an encrypted bundle and upload it to remote storage.

    Returns metadata: ``{key, size_bytes, created_at, retained}``.
    Best-effort retention pruning runs after a successful upload.
    """
    if client is None:
        client = _s3_client(cfg)
    created_at = datetime.now(UTC)
    # Create the bundle in memory (avoid touching disk on the VPS).
    buf = io.BytesIO()
    meta = create_bundle(data_dir, cfg.passphrase, out_path=Path("/tmp/_remote_backup.kmp"))
    bundle_bytes = Path(meta["path"]).read_bytes()
    Path(meta["path"]).unlink(missing_ok=True)
    key = _bundle_key(cfg, created_at)
    client.put_object(
        Bucket=cfg.bucket,
        Key=key,
        Body=bundle_bytes,
        ContentType="application/octet-stream",
    )
    pruned = _prune_old(cfg, client)
    return {
        "key": key,
        "size_bytes": len(bundle_bytes),
        "created_at": created_at.isoformat(),
        "files": meta.get("files", []),
        "pruned": pruned,
    }


def _prune_old(cfg: RemoteBackupConfig, client) -> int:
    """Delete bundles beyond ``retain`` count. Returns number deleted."""
    if cfg.retain <= 0:
        return 0
    try:
        resp = client.list_objects_v2(
            Bucket=cfg.bucket,
            Prefix=cfg.prefix,
        )
    except Exception:
        return 0
    items = sorted(
        (o for o in resp.get("Contents", []) if o["Key"].endswith(".kmp")),
        key=lambda o: o["LastModified"],
        reverse=True,
    )
    to_delete = [o["Key"] for o in items[cfg.retain:]]
    if not to_delete:
        return 0
    client.delete_objects(
        Bucket=cfg.bucket,
        Delete={"Objects": [{"Key": k} for k in to_delete]},
    )
    return len(to_delete)


def list_remote_bundles(cfg: RemoteBackupConfig, *, client=None) -> list[dict]:
    """List remote bundles newest first."""
    if client is None:
        client = _s3_client(cfg)
    resp = client.list_objects_v2(
        Bucket=cfg.bucket,
        Prefix=cfg.prefix,
    )
    items = sorted(
        resp.get("Contents", []),
        key=lambda o: o["LastModified"],
        reverse=True,
    )
    return [
        {
            "key": o["Key"],
            "size_bytes": o["Size"],
            "last_modified": o["LastModified"].isoformat(),
        }
        for o in items
        if o["Key"].endswith(".kmp")
    ]


def download_bundle(
    cfg: RemoteBackupConfig,
    key: str,
    out_path: Path,
    *,
    client=None,
) -> dict:
    """Download a remote bundle to ``out_path``. Returns metadata."""
    if client is None:
        client = _s3_client(cfg)
    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(cfg.bucket, key, str(out_path))
    header = read_bundle_header(out_path)
    return {
        "key": key,
        "path": str(out_path),
        "size_bytes": out_path.stat().st_size,
        "bundle_created_at": header.get("created_at"),
    }


def restore_from_remote(
    cfg: RemoteBackupConfig,
    data_dir: Path,
    *,
    key: str | None = None,
    force: bool = False,
    client=None,
) -> dict:
    """Download the latest (or specified) remote bundle and import it.

    Returns the ``import_bundle`` result dict plus download metadata.
    """
    if client is None:
        client = _s3_client(cfg)
    if key is None:
        bundles = list_remote_bundles(cfg, client=client)
        if not bundles:
            raise RemoteBackupError("No remote bundles found")
        key = bundles[0]["key"]
    buf = io.BytesIO()
    client.download_fileobj(cfg.bucket, key, buf)
    buf.seek(0)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = data_dir / "_remote_restore.kmp"
    tmp_path.write_bytes(buf.getvalue())
    try:
        result = import_bundle(tmp_path, cfg.passphrase, data_dir, force=force)
    except BundlePassphraseError as exc:
        raise RemoteBackupError(
            "Remote bundle passphrase mismatch — check remote_backup.passphrase"
        ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)
    result["restored_from_key"] = key
    return result


__all__ = [
    "RemoteBackupConfig",
    "RemoteBackupError",
    "upload_bundle",
    "list_remote_bundles",
    "download_bundle",
    "restore_from_remote",
]
