"""Tests for RuntimeStateStore."""

from __future__ import annotations

import tempfile
from pathlib import Path

from kompany.state.database import Database
from kompany.state.runtime import RuntimeStateStore


def _make_store():
    tmp = tempfile.mkdtemp()
    db = Database(Path(tmp))
    return RuntimeStateStore(db), db


def test_default_state_is_running():
    store, _ = _make_store()
    assert store.get() == {"state": "running", "reason": None, "since": None}


def test_set_suspended_round_trip():
    store, _ = _make_store()
    snap = store.set("suspended", reason="quota_exhausted")

    assert snap["state"] == "suspended"
    assert snap["reason"] == "quota_exhausted"
    assert snap["since"]  # non-empty timestamp


def test_set_updates_since_only_on_transition():
    store, _ = _make_store()
    first = store.set("suspended", reason="manual")
    second = store.set("suspended", reason="manual")

    # Same state -> since unchanged.
    assert second["since"] == first["since"]

    third = store.set("running")
    assert third["since"] != first["since"]


def test_state_persists_across_store_instances():
    store, db = _make_store()
    store.set("suspended", reason="quota")

    fresh = RuntimeStateStore(db)
    assert fresh.get()["state"] == "suspended"
    assert fresh.get()["reason"] == "quota"
