"""Tests for the encrypted export/import bundle and handoff tombstone."""

from __future__ import annotations

import os
import stat

import pytest

from kompany.state.database import Database
from kompany.state.export_bundle import (
    BundlePassphraseError,
    create_bundle,
    exported_marker_path,
    import_bundle,
    read_bundle_header,
    read_exported_marker,
    write_exported_marker,
)

PASS = "correct horse battery staple"


def _seed_source(data_dir):
    db = Database(data_dir)
    db.execute(
        "INSERT INTO ledger (amount, balance_after, description, category) VALUES (?, ?, ?, ?)",
        (42.0, 42.0, "seed", "operational"),
    )
    db.commit()
    db.close()
    (data_dir / "config.yaml").write_text("company_name: TestCo\n")
    (data_dir / ".vault-master.key").write_text("fake-vault-key\n")
    (data_dir / "extra-git-crypt.key").write_bytes(b"\x00GITCRYPTKEY")


def test_export_import_round_trip(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    _seed_source(src)

    bundle = tmp_path / "company.kmp"
    meta = create_bundle(src, PASS, bundle)
    assert bundle.exists()
    assert meta["size_bytes"] > 0
    assert set(meta["files"]) == {
        "kompany.db", "config.yaml", ".vault-master.key", "extra-git-crypt.key",
    }

    result = import_bundle(bundle, PASS, dst)
    assert sorted(result["files"]) == sorted(meta["files"])
    assert (dst / "config.yaml").read_text() == "company_name: TestCo\n"
    assert (dst / ".vault-master.key").read_text() == "fake-vault-key\n"
    assert (dst / "extra-git-crypt.key").read_bytes() == b"\x00GITCRYPTKEY"

    db = Database(dst)
    rows = db.execute("SELECT description, amount FROM ledger").fetchall()
    db.close()
    assert rows[0]["description"] == "seed"
    assert rows[0]["amount"] == 42.0


def test_import_secret_files_are_0600(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    _seed_source(src)
    bundle = tmp_path / "b.kmp"
    create_bundle(src, PASS, bundle)
    import_bundle(bundle, PASS, dst)
    for name in (".vault-master.key", "extra-git-crypt.key"):
        mode = stat.S_IMODE(os.stat(dst / name).st_mode)
        assert mode == 0o600, f"{name} should be 0600, got {oct(mode)}"


def test_import_wrong_passphrase_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _seed_source(src)
    bundle = tmp_path / "b.kmp"
    create_bundle(src, PASS, bundle)
    with pytest.raises(BundlePassphraseError):
        import_bundle(bundle, "wrong", tmp_path / "dst")


def test_import_refuses_existing_db_without_force(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    _seed_source(src)
    _seed_source(dst)
    bundle = tmp_path / "b.kmp"
    create_bundle(src, PASS, bundle)
    with pytest.raises(FileExistsError):
        import_bundle(bundle, PASS, dst)
    # --force succeeds
    result = import_bundle(bundle, PASS, dst, force=True)
    assert "kompany.db" in result["files"]


def test_empty_passphrase_rejected(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _seed_source(src)
    with pytest.raises(ValueError):
        create_bundle(src, "", tmp_path / "b.kmp")


def test_bundle_header_readable_without_passphrase(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _seed_source(src)
    bundle = tmp_path / "b.kmp"
    create_bundle(src, PASS, bundle)
    header = read_bundle_header(bundle)
    assert header["version"] == 1
    assert header["kdf"] == "pbkdf2-sha256"
    assert "kompany.db" in header["files"]


def test_bundle_payload_is_not_plaintext(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _seed_source(src)
    bundle = tmp_path / "b.kmp"
    create_bundle(src, PASS, bundle)
    raw = bundle.read_bytes()
    assert b"fake-vault-key" not in raw
    assert b"TestCo" not in raw


def test_exported_marker_round_trip(tmp_path):
    assert read_exported_marker(tmp_path) is None
    meta = write_exported_marker(tmp_path, "/tmp/b.kmp")
    read = read_exported_marker(tmp_path)
    assert read["exported_at"] == meta["exported_at"]
    assert read["bundle_path"] == "/tmp/b.kmp"


def test_import_clears_exported_marker(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    _seed_source(src)
    write_exported_marker(dst, "/old/bundle.kmp")
    bundle = tmp_path / "b.kmp"
    create_bundle(src, PASS, bundle)
    import_bundle(bundle, PASS, dst)
    assert read_exported_marker(dst) is None
    assert not exported_marker_path(dst).exists()


def test_daemon_run_refuses_tombstoned_data_dir(tmp_path):
    from kompany.core.daemon_ops import run_daemon

    write_exported_marker(tmp_path, "/tmp/b.kmp")
    result = run_daemon(data_dir=tmp_path)
    assert result["started"] is False
    assert result["source"] == "exported"
    assert "handed off" in result["message"]


def test_ticker_idles_on_tombstone(tmp_path, monkeypatch):
    """A tombstoned data_dir records idle_exported ticks and runs no actions."""
    from kompany.core.engine import KompanyEngine

    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    engine = KompanyEngine()
    try:
        write_exported_marker(tmp_path, "/tmp/b.kmp")
        row = engine.ticker.tick_once()
        assert row["outcome"] == "idle_exported"
        assert row["actions"] == []
    finally:
        engine.db.close()


def test_engine_export_company_handoff(tmp_path, monkeypatch):
    from kompany.core.engine import KompanyEngine

    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    engine = KompanyEngine()
    try:
        out = tmp_path / "out" / "company.kmp"
        meta = engine.export_company(PASS, out_path=str(out), handoff=True)
        assert meta["handoff"] is True
        assert out.exists()
        assert read_exported_marker(tmp_path) is not None
        assert engine.get_runtime_state()["state"] == "suspended"
        events = [e["event_type"] for e in engine.audit.recent(limit=20)]
        assert "export.created" in events
        assert "export.handoff" in events
    finally:
        engine.db.close()
