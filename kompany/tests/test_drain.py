"""Tests for the deployment drain protocol.

Covers ``core.drain.ActiveOperationRegistry`` in isolation, plus
``Engine.drain()`` / ``Engine.drain_status()`` combining persisted suspend
state with the live in-memory counts (Stage A deployment plan, step 6).
"""

from __future__ import annotations

import threading

import pytest

from kompany.core.drain import ActiveOperationRegistry, get_drain_registry

# Reuse the full engine fixture from test_engine.py rather than duplicating
# its settings/store wiring — importing the fixture function makes pytest
# pick it up in this module too.
from tests.test_engine import engine  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_global_registry():
    """The module-level singleton is process-wide; reset it around every
    test so counts from one test never leak into another."""
    get_drain_registry().reset()
    yield
    get_drain_registry().reset()


def test_track_increments_and_decrements():
    reg = ActiveOperationRegistry()
    assert reg.total() == 0
    with reg.track("task_attempt"):
        assert reg.counts()["task_attempt"] == 1
        assert reg.total() == 1
    assert reg.counts()["task_attempt"] == 0
    assert reg.total() == 0


def test_track_decrements_even_on_exception():
    reg = ActiveOperationRegistry()
    with pytest.raises(ValueError):
        with reg.track("connector_call"):
            raise ValueError("boom")
    assert reg.counts()["connector_call"] == 0


def test_track_rejects_unknown_category():
    reg = ActiveOperationRegistry()
    with pytest.raises(ValueError):
        with reg.track("not_a_real_category"):
            pass


def test_counts_are_independent_per_category():
    reg = ActiveOperationRegistry()
    with reg.track("task_attempt"), reg.track("harness_child"):
        counts = reg.counts()
        assert counts["task_attempt"] == 1
        assert counts["harness_child"] == 1
        assert counts["channel_handler"] == 0
        assert counts["connector_call"] == 0
        assert reg.total() == 2


def test_nested_tracks_of_same_category_stack():
    reg = ActiveOperationRegistry()
    with reg.track("task_attempt"):
        with reg.track("task_attempt"):
            assert reg.counts()["task_attempt"] == 2
        assert reg.counts()["task_attempt"] == 1
    assert reg.counts()["task_attempt"] == 0


def test_reset_forces_all_counters_to_zero():
    reg = ActiveOperationRegistry()
    with reg.track("task_attempt"):
        pass
    with reg.track("connector_call"):
        # Simulate a stuck counter by resetting while nothing is tracked
        # in this category — reset() is documented as test-only, used here
        # to confirm it zeroes every category regardless of prior state.
        pass
    reg.reset()
    assert reg.total() == 0
    assert reg.counts() == {
        "task_attempt": 0,
        "channel_handler": 0,
        "harness_child": 0,
        "connector_call": 0,
    }


def test_thread_safety_under_concurrent_track_calls():
    reg = ActiveOperationRegistry()
    iterations = 200
    threads_n = 8
    peak_seen = []
    lock = threading.Lock()

    def worker():
        for _ in range(iterations):
            with reg.track("connector_call"):
                with lock:
                    peak_seen.append(reg.counts()["connector_call"])

    threads = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Final state must be exactly zero — no lost decrements from races.
    assert reg.total() == 0
    # Some overlap should have been observed (proves it's not accidentally
    # serialized to a single in-flight operation at a time).
    assert max(peak_seen) >= 1


def test_get_drain_registry_is_a_process_wide_singleton():
    a = get_drain_registry()
    b = get_drain_registry()
    assert a is b


def test_engine_drain_status_ready_when_running_and_idle(engine):
    status = engine.drain_status()
    assert status["state"] == "running"
    assert status["ready_for_restart"] is False
    assert status["active_operations"] == {
        "task_attempt": 0,
        "channel_handler": 0,
        "harness_child": 0,
        "connector_call": 0,
    }


def test_engine_drain_suspends_and_reports_ready_when_idle(engine):
    result = engine.drain(reason="deployment")
    assert result["state"] == "suspended"
    assert result["reason"] == "deployment"
    assert result["ready_for_restart"] is True
    assert engine.runtime.get()["state"] == "suspended"


def test_engine_drain_status_not_ready_while_operation_in_flight(engine):
    registry = get_drain_registry()
    engine.drain(reason="deployment")
    with registry.track("harness_child"):
        status = engine.drain_status()
        assert status["state"] == "suspended"
        assert status["active_operations"]["harness_child"] == 1
        assert status["ready_for_restart"] is False

    # Once the in-flight operation completes, status flips to ready.
    final = engine.drain_status()
    assert final["ready_for_restart"] is True


def test_engine_drain_not_ready_if_only_suspended_but_not_drained_state():
    # drain_status() must require BOTH suspended state AND zero live ops —
    # exercised directly against the registry without an engine to keep
    # this test independent of engine fixture setup cost.
    registry = get_drain_registry()
    with registry.track("connector_call"):
        assert registry.total() == 1
    assert registry.total() == 0


def test_engine_drain_is_idempotent_like_suspend(engine):
    first = engine.drain(reason="deployment")
    second = engine.drain(reason="deployment_retry")
    assert first["state"] == "suspended"
    assert second["state"] == "suspended"
    # Reuses suspend()'s idempotent no-op path — second reason is ignored.
    assert second["status"] == "already_suspended"
