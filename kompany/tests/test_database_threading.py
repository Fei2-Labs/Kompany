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


def test_concurrent_execute_does_not_corrupt_connection(tmp_path):
    """Regression: concurrent db.execute() from many threads must not
    corrupt the shared connection.

    Before the RLock in Database.execute/commit, two threadpool workers
    hitting the connection at once raised ``InterfaceError: bad
    parameter or other API misuse`` and left the connection in a state
    where every later call hung — which is why the remote settings page
    only rendered the sections whose endpoints won the race (Telegram
    worked, LLM model / founder profile spun forever).
    """
    db = Database(tmp_path)

    errors: list[Exception] = []

    def worker(i: int):
        try:
            for _ in range(20):
                db.execute(
                    "INSERT INTO company_config (key, value, updated_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (f"k{i}_{_}", "v"),
                )
                db.commit()
                db.execute(
                    "SELECT value FROM company_config WHERE key = ?",
                    (f"k{i}_{_}",),
                ).fetchone()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent DB use raised: {errors}"
    # Connection still usable after the storm.
    row = db.execute("SELECT COUNT(*) AS n FROM company_config").fetchone()
    assert row["n"] == 8 * 20
