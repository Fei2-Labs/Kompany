"""Tests for the run-id ContextVar plumbing."""

from __future__ import annotations

import asyncio
import re
import threading

import pytest

from kompany.core.run_context import (
    RUN_ID_PATTERN,
    current_run_id,
    is_valid_run_id,
    new_run_id,
    parent_run_id,
    reset_run_id,
    run_scope,
    set_run_id,
)


_RUN_ID_RE = re.compile(RUN_ID_PATTERN)


def test_new_run_id_format():
    rid = new_run_id()
    assert _RUN_ID_RE.match(rid), rid
    assert rid.startswith("r_")
    assert len(rid) == 28  # "r_" + 26 base32 chars


def test_new_run_id_unique():
    ids = {new_run_id() for _ in range(500)}
    assert len(ids) == 500


def test_is_valid_run_id():
    assert is_valid_run_id(new_run_id())
    assert not is_valid_run_id(None)
    assert not is_valid_run_id("")
    assert not is_valid_run_id("r_short")
    assert not is_valid_run_id("R_" + "0" * 26)  # wrong prefix case
    assert not is_valid_run_id("r_" + "I" * 26)  # forbidden Crockford letter
    assert not is_valid_run_id("r_" + "L" * 26)
    assert not is_valid_run_id("r_" + "O" * 26)
    assert not is_valid_run_id("r_" + "U" * 26)


def test_current_run_id_none_outside_scope():
    assert current_run_id() is None
    assert parent_run_id() is None


def test_run_scope_sets_and_clears():
    assert current_run_id() is None
    with run_scope() as rid:
        assert current_run_id() == rid
        assert parent_run_id() is None
    assert current_run_id() is None


def test_run_scope_explicit_id():
    explicit = new_run_id()
    with run_scope(explicit) as rid:
        assert rid == explicit
        assert current_run_id() == explicit


def test_run_scope_rejects_bad_id():
    with pytest.raises(ValueError):
        with run_scope("not-a-ulid"):
            pass


def test_set_run_id_rejects_bad_id():
    with pytest.raises(ValueError):
        set_run_id("nope")


def test_set_and_reset_run_id_manual():
    rid = new_run_id()
    token = set_run_id(rid)
    try:
        assert current_run_id() == rid
    finally:
        reset_run_id(token)
    assert current_run_id() is None


def test_nested_run_scope_records_parent():
    with run_scope() as outer:
        assert parent_run_id() is None
        with run_scope() as inner:
            assert current_run_id() == inner
            assert parent_run_id() == outer
        # After inner exits parent should restore.
        assert current_run_id() == outer
        assert parent_run_id() is None


def test_run_scope_explicit_parent_overrides_active():
    """A directive can record a parent that isn't the current scope."""
    forced_parent = new_run_id()
    with run_scope() as outer:
        with run_scope(parent=forced_parent) as child:
            assert current_run_id() == child
            assert parent_run_id() == forced_parent
        assert parent_run_id() is None  # restored to outer's frame
        assert current_run_id() == outer


def test_exception_in_scope_still_resets():
    with pytest.raises(RuntimeError):
        with run_scope():
            assert current_run_id() is not None
            raise RuntimeError("boom")
    assert current_run_id() is None


def test_async_scope_isolated_between_tasks():
    """ContextVars copy per-task — sibling tasks see distinct run ids."""

    captured: dict[str, str | None] = {}

    async def child(name: str, rid: str):
        with run_scope(rid):
            await asyncio.sleep(0)
            captured[name] = current_run_id()

    async def main():
        a, b = new_run_id(), new_run_id()
        await asyncio.gather(child("a", a), child("b", b))
        return a, b

    a, b = asyncio.run(main())
    assert captured["a"] == a
    assert captured["b"] == b
    assert a != b


def test_threads_do_not_inherit_run_id():
    """contextvars don't auto-copy across raw threads (Python's documented
    behaviour). The store layer treats that as "no active run", which is
    the correct conservative default — background workers must open their
    own scope."""

    with run_scope():
        captured: list[str | None] = []

        def worker():
            captured.append(current_run_id())

        t = threading.Thread(target=worker)
        t.start()
        t.join()

    assert captured == [None]
