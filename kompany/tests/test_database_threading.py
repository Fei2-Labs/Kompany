"""Regression: the SQLite connection must be usable across threads.

FastAPI serves sync endpoints and runs BackgroundTasks in a worker
threadpool, so the engine's shared connection is created on one thread
and used from others (e.g. the post-onboarding kickoff runs
execute_project in a separate thread). Before check_same_thread=False
this raised ``ProgrammingError: SQLite objects created in a thread can
only be used in that same thread`` and broke the connection for every
later request — the dashboard then showed cash $0 / days -- because
/status, /projects, /targets all 500'd.
"""

from __future__ import annotations

import threading

from kompany.state.database import Database


def test_connection_usable_from_another_thread(tmp_path):
    db = Database(tmp_path)
    # Bind the connection on the main thread first.
    db.conn.execute("SELECT 1").fetchone()

    errors: list[Exception] = []

    def worker():
        try:
            # A write + read from a different thread, mirroring the
            # background kickoff task touching projects/ledger.
            db.conn.execute(
                "INSERT INTO company_config (key, value, updated_at) "
                "VALUES ('thread_test', 'ok', datetime('now'))"
            )
            db.conn.commit()
            db.conn.execute(
                "SELECT value FROM company_config WHERE key = 'thread_test'"
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert errors == [], f"cross-thread DB use raised: {errors}"
    # Main thread still works after the worker touched the connection.
    row = db.conn.execute(
        "SELECT value FROM company_config WHERE key = 'thread_test'"
    ).fetchone()
    assert row["value"] == "ok"
