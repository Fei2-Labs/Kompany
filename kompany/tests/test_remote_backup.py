"""Tests for the S3-compatible remote backup adapter."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kompany.state.export_bundle import create_bundle, import_bundle
from kompany.state.remote_backup import (
    RemoteBackupConfig,
    RemoteBackupError,
    download_bundle,
    list_remote_bundles,
    restore_from_remote,
    upload_bundle,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _valid_cfg_dict(**overrides):
    d = {
        "endpoint_url": "https://s3.example.com",
        "bucket": "kompany-backups",
        "access_key_id": "AKIA TEST",
        "secret_access_key": "secret",
        "passphrase": "test-passphrase",
    }
    d.update(overrides)
    return d


def test_config_from_dict_valid():
    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict())
    assert cfg.endpoint_url == "https://s3.example.com"
    assert cfg.bucket == "kompany-backups"
    assert cfg.region == "auto"
    assert cfg.prefix == "kompany/"
    assert cfg.retain == 7


def test_config_from_dict_missing_fields():
    with pytest.raises(RemoteBackupError, match="missing required"):
        RemoteBackupConfig.from_dict({"endpoint_url": "https://s3.example.com"})


def test_config_from_dict_overrides():
    cfg = RemoteBackupConfig.from_dict(
        _valid_cfg_dict(region="eu-west-1", prefix="backups/", retain=3)
    )
    assert cfg.region == "eu-west-1"
    assert cfg.prefix == "backups/"
    assert cfg.retain == 3


def test_config_is_configured():
    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict())
    assert cfg.is_configured()


# ---------------------------------------------------------------------------
# Upload / list / download / restore (mocked S3 client)
# ---------------------------------------------------------------------------

def _fake_data_dir(tmp_path: Path) -> Path:
    """Create a minimal data_dir with a real SQLite DB + config."""
    import sqlite3
    data_dir = tmp_path / "kompany-data"
    data_dir.mkdir()
    conn = sqlite3.connect(str(data_dir / "kompany.db"))
    conn.execute("CREATE TABLE company (name TEXT)")
    conn.execute("INSERT INTO company VALUES ('Test')")
    conn.commit()
    conn.close()
    (data_dir / "config.yaml").write_text("company:\n  name: Test\n")
    return data_dir


def _mock_s3_client():
    """A mock S3 client that stores objects in a dict."""
    store: dict[str, bytes] = {}
    timestamps: dict[str, datetime] = {}
    _counter = [0]

    client = MagicMock()

    def put_object(Bucket, Key, Body, **kw):
        store[Key] = Body if isinstance(Body, bytes) else Body.read()
        _counter[0] += 1
        timestamps[Key] = datetime(2026, 1, 1, tzinfo=UTC).replace(
            hour=0, minute=_counter[0]
        )

    def list_objects_v2(Bucket, Prefix, **kw):
        items = []
        for key, data in sorted(store.items()):
            if key.startswith(Prefix) and key.endswith(".kmp"):
                items.append({
                    "Key": key,
                    "Size": len(data),
                    "LastModified": timestamps[key],
                })
        return {"Contents": items}

    def download_file(Bucket, Key, path):
        Path(path).write_bytes(store[Key])

    def download_fileobj(Bucket, Key, buf):
        buf.write(store[Key])

    def delete_objects(Bucket, Delete):
        for obj in Delete["Objects"]:
            store.pop(obj["Key"], None)

    client.put_object.side_effect = put_object
    client.list_objects_v2.side_effect = list_objects_v2
    client.download_file.side_effect = download_file
    client.download_fileobj.side_effect = download_fileobj
    client.delete_objects.side_effect = delete_objects
    client._store = store
    return client


def test_upload_bundle_creates_and_stores(tmp_path):
    data_dir = _fake_data_dir(tmp_path)
    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict())
    client = _mock_s3_client()

    result = upload_bundle(cfg, data_dir, client=client)

    assert result["key"].startswith("kompany/kompany-")
    assert result["key"].endswith(".kmp")
    assert result["size_bytes"] > 0
    assert result["key"] in client._store
    # Bundle should be encrypted (not plaintext)
    payload = client._store[result["key"]]
    assert b"KOMPANYBUNDLE1" in payload
    assert b"fake-db-content" not in payload


def test_upload_bundle_prunes_old(tmp_path):
    data_dir = _fake_data_dir(tmp_path)
    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict(retain=2))
    client = _mock_s3_client()

    # Upload 3 bundles
    keys = []
    for _ in range(3):
        result = upload_bundle(cfg, data_dir, client=client)
        keys.append(result["key"])

    # Only the last 2 should remain
    remaining = list_remote_bundles(cfg, client=client)
    assert len(remaining) == 2
    remaining_keys = {b["key"] for b in remaining}
    assert keys[-1] in remaining_keys
    assert keys[-2] in remaining_keys
    assert keys[0] not in remaining_keys


def test_list_remote_bundles(tmp_path):
    data_dir = _fake_data_dir(tmp_path)
    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict())
    client = _mock_s3_client()

    upload_bundle(cfg, data_dir, client=client)
    upload_bundle(cfg, data_dir, client=client)

    bundles = list_remote_bundles(cfg, client=client)
    assert len(bundles) == 2
    for b in bundles:
        assert b["key"].endswith(".kmp")
        assert b["size_bytes"] > 0


def test_download_bundle(tmp_path):
    data_dir = _fake_data_dir(tmp_path)
    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict())
    client = _mock_s3_client()

    up = upload_bundle(cfg, data_dir, client=client)
    out = tmp_path / "downloaded.kmp"
    result = download_bundle(cfg, up["key"], out, client=client)

    assert result["key"] == up["key"]
    assert result["size_bytes"] == up["size_bytes"]
    assert out.exists()
    assert out.read_bytes() == client._store[up["key"]]


def test_restore_from_remote_latest(tmp_path):
    data_dir = _fake_data_dir(tmp_path)
    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict())
    client = _mock_s3_client()

    # Upload a bundle
    upload_bundle(cfg, data_dir, client=client)

    # Restore to a fresh data_dir
    restore_dir = tmp_path / "restored"
    result = restore_from_remote(cfg, restore_dir, client=client)

    assert result["restored_from_key"].endswith(".kmp")
    assert "kompany.db" in result["files"]
    assert (restore_dir / "kompany.db").exists()
    assert (restore_dir / "config.yaml").exists()


def test_restore_from_remote_specific_key(tmp_path):
    data_dir = _fake_data_dir(tmp_path)
    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict())
    client = _mock_s3_client()

    up = upload_bundle(cfg, data_dir, client=client)
    restore_dir = tmp_path / "restored"
    result = restore_from_remote(cfg, restore_dir, key=up["key"], client=client)

    assert result["restored_from_key"] == up["key"]


def test_restore_from_remote_no_bundles(tmp_path):
    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict())
    client = _mock_s3_client()

    with pytest.raises(RemoteBackupError, match="No remote bundles"):
        restore_from_remote(cfg, tmp_path / "restore", client=client)


def test_restore_from_remote_wrong_passphrase(tmp_path):
    data_dir = _fake_data_dir(tmp_path)
    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict(passphrase="pass1"))
    client = _mock_s3_client()

    upload_bundle(cfg, data_dir, client=client)

    # Try to restore with a different passphrase
    cfg2 = RemoteBackupConfig.from_dict(_valid_cfg_dict(passphrase="wrong"))
    with pytest.raises(RemoteBackupError, match="passphrase mismatch"):
        restore_from_remote(cfg2, tmp_path / "restore", client=client)


def test_upload_bundle_boto3_missing(tmp_path):
    data_dir = _fake_data_dir(tmp_path)
    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict())

    with patch("builtins.__import__", side_effect=ImportError("no boto3")):
        with pytest.raises(RemoteBackupError, match="boto3 is not installed"):
            upload_bundle(cfg, data_dir)


# ---------------------------------------------------------------------------
# Round-trip: export → upload → download → import = full state intact
# ---------------------------------------------------------------------------

def test_round_trip_full_state(tmp_path):
    """Export → upload → download → import preserves DB + config."""
    data_dir = _fake_data_dir(tmp_path)
    # Add a vault key
    (data_dir / ".vault-master.key").write_text("vault-secret-key")

    cfg = RemoteBackupConfig.from_dict(_valid_cfg_dict())
    client = _mock_s3_client()

    # Upload
    up = upload_bundle(cfg, data_dir, client=client)

    # Restore to fresh dir
    restore_dir = tmp_path / "restored"
    result = restore_from_remote(cfg, restore_dir, key=up["key"], client=client)

    assert "kompany.db" in result["files"]
    assert "config.yaml" in result["files"]
    assert ".vault-master.key" in result["files"]
    # DB content is a real SQLite file — verify the company row survived
    import sqlite3
    conn = sqlite3.connect(str(restore_dir / "kompany.db"))
    row = conn.execute("SELECT name FROM company").fetchone()
    conn.close()
    assert row == ("Test",)
    assert (restore_dir / "config.yaml").read_text() == "company:\n  name: Test\n"
    assert (restore_dir / ".vault-master.key").read_text() == "vault-secret-key"
