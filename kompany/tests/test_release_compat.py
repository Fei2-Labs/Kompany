"""Release-blocking compatibility tests (Stage A recovery, deployment plan
Stage A step 5): "Core and Pro wheels, plugin discovery, browser tools,
SQLite concurrency, production-schema migration, package-data smoke
install, and multi-agent regression."

These exercise failure modes an editable/source-tree test run cannot catch:
a built, non-editable wheel installed into a scratch venv is the only way
to notice a missing package-data glob (e.g. a forgotten ``*.mjs`` pattern)
or an entry point that only resolves from the source checkout. The wheel
build + install tests are slow (network + compiler-free but still
seconds-to-a-minute) and are marked so a fast default `pytest` run can skip
them; CI's release-gating job runs the full module explicitly.

"Multi-agent regression" is the full existing suite (already exercised by
plain ``pytest``) — nothing new needed here beyond making sure the wheel
build doesn't silently drop any of the modules that regression depends on,
which the plugin-discovery-from-wheel test below covers.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.compat

CORE_ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, check=True, capture_output=True, text=True, timeout=300, **kwargs
    )


# ---------------------------------------------------------------------------
# Core wheel: build + non-editable install + import + entry points
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def core_wheel(tmp_path_factory) -> Path:
    """Build kompany's wheel once for this module's tests."""
    if shutil.which("uv") is None and not _has_build_module():
        pytest.skip("neither `uv` nor the `build` module is available to build a wheel")
    dist_dir = tmp_path_factory.mktemp("core-dist")
    if shutil.which("uv") is not None:
        _run(["uv", "build", "--wheel", "-o", str(dist_dir)], cwd=CORE_ROOT)
    else:
        _run([sys.executable, "-m", "build", "--wheel", "-o", str(dist_dir)], cwd=CORE_ROOT)
    wheels = list(dist_dir.glob("*.whl"))
    assert wheels, f"no wheel produced in {dist_dir}"
    return wheels[0]


def _has_build_module() -> bool:
    try:
        import build  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.fixture(scope="module")
def core_wheel_venv(tmp_path_factory, core_wheel: Path) -> Path:
    """A scratch venv with the built Core wheel installed non-editable —
    proves the release artifact is self-contained, not just the source
    checkout with its editable-install sys.path shortcuts."""
    venv_dir = tmp_path_factory.mktemp("core-venv")
    _run([sys.executable, "-m", "venv", str(venv_dir)])
    pip = venv_dir / "bin" / "pip"
    _run([str(pip), "install", "-q", str(core_wheel)])
    return venv_dir


@pytest.mark.slow
def test_core_wheel_installs_and_imports(core_wheel_venv: Path):
    python = core_wheel_venv / "bin" / "python3"
    result = _run(
        [
            str(python),
            "-c",
            "import kompany; from kompany.plugins.loader import discover; "
            "d = discover(); assert 'integration' in d, d",
        ]
    )
    assert result.returncode == 0


@pytest.mark.slow
def test_core_wheel_builtin_email_integration_discovered(core_wheel_venv: Path):
    """A non-editable install must still find Core's own builtin
    contributions (see loader._BUILTIN_CONTRIBUTIONS) — these are wired by
    hardcoded module path, not an entry point, so a packaging mistake that
    excludes ``kompany.integrations.email_smtp`` from the wheel would only
    show up here, never in an editable-install test run."""
    python = core_wheel_venv / "bin" / "python3"
    result = _run(
        [
            str(python),
            "-c",
            "from kompany.plugins.loader import discover; d = discover(); "
            "names = {type(i).__name__ for i in d['integration']}; "
            "assert 'EmailIntegration' in names and 'ResendIntegration' in names, names",
        ]
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Pro plugin discovery from an installed (non-editable) wheel
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_pro_package_discovered_from_installed_wheel(tmp_path, core_wheel: Path):
    """Package-data smoke install: build kompany-pro's own wheel (from
    whatever checkout of it CI has staged next to this repo, or skip if
    none is available locally) and confirm the browser-tool ``.mjs``
    scripts and integration entry points survive being installed
    non-editable — the exact failure mode Stage A hit once already
    (forgotten package-data glob for ``.mjs`` files)."""
    pro_root = os.environ.get("KOMPANY_PRO_CHECKOUT")
    if not pro_root or not Path(pro_root).is_dir():
        pytest.skip(
            "set KOMPANY_PRO_CHECKOUT to a kompany-pro checkout path to run "
            "this cross-repo package-data smoke test"
        )
    pro_root = Path(pro_root)
    venv_dir = tmp_path / "venv"
    _run([sys.executable, "-m", "venv", str(venv_dir)])
    pip = venv_dir / "bin" / "pip"
    _run([str(pip), "install", "-q", str(core_wheel)])
    _run([str(pip), "install", "-q", str(pro_root)])
    python = venv_dir / "bin" / "python3"
    result = _run(
        [
            str(python),
            "-c",
            "from importlib.metadata import entry_points\n"
            "eps = {ep.name for ep in entry_points(group='kompany.integrations')}\n"
            "assert 'linkedin' in eps, eps\n"
            "import kompany_pro.integrations.linkedin_growth as m\n"
            "scripts = list((__import__('pathlib').Path(m.__file__).parent / 'scripts').glob('*.mjs'))\n"
            "assert scripts, 'no .mjs scripts survived the wheel — package-data glob regression'\n",
        ]
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# SQLite concurrency: two threads through Database's own lock/WAL wiring
# ---------------------------------------------------------------------------


def test_database_concurrent_writes_no_corruption(tmp_path):
    """Regression guard for the "database is locked" / silent state-machine
    corruption bug this session already fixed once (see Database.__init__
    docstring: concurrent execute/commit from two threads without the
    RLock raised InterfaceError and made the connection unusable). Fire
    many concurrent writers through the real ``Database`` wrapper and
    confirm every write lands and the file passes ``PRAGMA integrity_check``."""
    from kompany.state.database import Database

    db = Database(tmp_path)
    db.execute(
        "CREATE TABLE IF NOT EXISTS _compat_probe (id INTEGER PRIMARY KEY, worker INTEGER, seq INTEGER)"
    )
    db.commit()

    n_workers = 8
    n_writes_per_worker = 25
    errors: list[BaseException] = []

    def _writer(worker_id: int) -> None:
        try:
            for seq in range(n_writes_per_worker):
                db.execute(
                    "INSERT INTO _compat_probe (worker, seq) VALUES (?, ?)",
                    (worker_id, seq),
                )
                db.commit()
        except BaseException as exc:  # noqa: BLE001 — captured for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(i,)) for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent writers raised: {errors}"
    row_count = db.execute("SELECT COUNT(*) FROM _compat_probe").fetchone()[0]
    assert row_count == n_workers * n_writes_per_worker

    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    assert integrity == "ok"
    db.close()


# ---------------------------------------------------------------------------
# Production-schema migration: an old pre-migration DB must upgrade cleanly
# ---------------------------------------------------------------------------


def test_migrations_upgrade_legacy_schema_without_data_loss(tmp_path):
    """Simulates the shape of a real production database captured *before*
    the columns/tables ``run_migrations`` adds (agent_memories.metadata /
    pattern_key / access_count, agent_status.project_id, tool_authorizations,
    ...). We build this synthetically rather than shipping a real
    production ``kompany.db`` snapshot into the repo (that file contains
    live business data and must never enter git history — see the Stage A
    forensic-copy secret-redaction precedent this session already applied
    to server script tarballs).

    Mirrors the actual rehearsal already performed against a live
    production snapshot during the Stage A audit: integrity stayed `ok`,
    all pre-existing row counts were preserved, and new tables/columns were
    additive only.
    """
    from kompany.state.database_parts.migrations import run_migrations
    from kompany.state.database_parts.schema import _SCHEMA

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    # _SCHEMA is the base DDL every migration assumes already exists
    # (see schema.py's own docstring: "initial schema"); migrations.py only
    # ever adds columns/tables on top of it. Using the real base schema
    # here — rather than a hand-picked table subset — is what actually
    # reproduces a real pre-migration production database's shape.
    conn.executescript(_SCHEMA)
    conn.executescript(
        """
        INSERT INTO agent_memories (agent_role, content) VALUES
            ('ceo', 'legacy memory row one'),
            ('cmo', 'legacy memory row two'),
            ('cfo', 'legacy memory row three');
        INSERT INTO agent_status (agent_role, status) VALUES
            ('ceo', 'idle'), ('cmo', 'working');
        """
    )
    conn.commit()

    pre_memory_count = conn.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0]
    pre_status_count = conn.execute("SELECT COUNT(*) FROM agent_status").fetchone()[0]
    pre_memory_rows = conn.execute(
        "SELECT agent_role, content FROM agent_memories ORDER BY id"
    ).fetchall()

    run_migrations(conn)
    conn.commit()

    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    assert integrity == "ok"

    post_memory_count = conn.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0]
    post_status_count = conn.execute("SELECT COUNT(*) FROM agent_status").fetchone()[0]
    post_memory_rows = conn.execute(
        "SELECT agent_role, content FROM agent_memories ORDER BY id"
    ).fetchall()
    assert post_memory_count == pre_memory_count
    assert post_status_count == pre_status_count
    assert post_memory_rows == pre_memory_rows

    # New migration-added columns/tables must exist and be additive.
    memory_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_memories)")}
    for expected in ("metadata", "pattern_key", "updated_at", "access_count", "last_accessed_at"):
        assert expected in memory_cols, f"missing migrated column: {expected}"

    status_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_status)")}
    for expected in ("project_id", "project_type", "activity_kind"):
        assert expected in status_cols, f"missing migrated column: {expected}"

    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "tool_authorizations" in tables

    # Idempotency: re-running migrations against an already-migrated DB
    # must not error or duplicate anything.
    run_migrations(conn)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0] == pre_memory_count

    conn.close()
