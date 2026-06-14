"""Tests for the per-action-CLASS outward pre-authorization policy (ADR-0008 #2).

Covers the SQLite-backed store (default-gated seed, set/get round-trip) and
the pure resolver, including the hard floors that override an 'auto' policy
to gated (spend, founder_identity, cost over the founder cap).
"""

from __future__ import annotations

import pytest

from kompany.core.outward_policy import resolve_outward_mode
from kompany.state.database import Database
from kompany.state.outward_policies import (
    DEFAULT_ACTION_CLASSES,
    MODE_AUTO,
    MODE_GATED,
    OutwardActionPolicyStore,
)


@pytest.fixture
def store(tmp_path) -> OutwardActionPolicyStore:
    return OutwardActionPolicyStore(Database(tmp_path))


# ---------------------------------------------------------------------------
# Store: seed / get / set
# ---------------------------------------------------------------------------


def test_seed_defaults_all_gated(store):
    policies = store.list()
    assert {p.action_class for p in policies} == set(DEFAULT_ACTION_CLASSES)
    assert all(p.mode == MODE_GATED for p in policies)


def test_get_returns_gated_default(store):
    for cls in DEFAULT_ACTION_CLASSES:
        assert store.get(cls) == MODE_GATED


def test_get_unknown_class_defaults_gated(store):
    assert store.get("totally_unknown_class") == MODE_GATED


def test_set_and_get_round_trip(store):
    policy = store.set("ai_voice_post", MODE_AUTO, reason="trusted AI voice")
    assert policy.mode == MODE_AUTO
    assert policy.reason == "trusted AI voice"
    assert store.get("ai_voice_post") == MODE_AUTO


def test_set_rejects_bad_mode(store):
    with pytest.raises(ValueError):
        store.set("ai_voice_post", "yolo")


def test_seed_defaults_idempotent_keeps_auto(store):
    store.set("ai_voice_post", MODE_AUTO)
    # Re-seed (e.g. a second engine boot) must not clobber the founder's auto.
    store.seed_defaults()
    assert store.get("ai_voice_post") == MODE_AUTO


# ---------------------------------------------------------------------------
# Resolver matrix + hard floors
# ---------------------------------------------------------------------------


class FakeStore:
    """Returns a fixed mode for every class."""

    def __init__(self, mode: str):
        self._mode = mode

    def get(self, action_class: str) -> str:
        return self._mode


def test_resolve_gated_policy_stays_gated():
    s = FakeStore(MODE_GATED)
    assert resolve_outward_mode(s, "ai_voice_post") == MODE_GATED


def test_resolve_auto_policy_is_auto_when_clean():
    s = FakeStore(MODE_AUTO)
    assert resolve_outward_mode(s, "ai_voice_post") == MODE_AUTO
    assert resolve_outward_mode(s, "published_content") == MODE_AUTO


def test_hard_floor_spend_forces_gated_even_when_auto():
    s = FakeStore(MODE_AUTO)
    assert (
        resolve_outward_mode(s, "ai_voice_post", side_effect="spend")
        == MODE_GATED
    )


def test_hard_floor_founder_identity_forces_gated_even_when_auto():
    s = FakeStore(MODE_AUTO)
    assert resolve_outward_mode(s, "founder_identity") == MODE_GATED


def test_hard_floor_cost_over_cap_forces_gated():
    s = FakeStore(MODE_AUTO)
    assert (
        resolve_outward_mode(
            s, "ai_voice_post", estimated_cost_usd=5.0, founder_cost_cap=1.0
        )
        == MODE_GATED
    )


def test_cost_at_or_under_cap_stays_auto():
    s = FakeStore(MODE_AUTO)
    assert (
        resolve_outward_mode(
            s, "ai_voice_post", estimated_cost_usd=1.0, founder_cost_cap=1.0
        )
        == MODE_AUTO
    )


def test_resolver_fails_closed_on_store_error():
    class BoomStore:
        def get(self, action_class):
            raise RuntimeError("db down")

    assert resolve_outward_mode(BoomStore(), "ai_voice_post") == MODE_GATED


def test_resolver_against_real_store_full_matrix(store):
    # gated default → gated
    assert resolve_outward_mode(store, "ai_voice_post") == MODE_GATED
    # flip to auto → auto
    store.set("ai_voice_post", MODE_AUTO)
    assert resolve_outward_mode(store, "ai_voice_post") == MODE_AUTO
    # spend class can be set auto but the floor still gates it
    store.set("spend", MODE_AUTO)
    assert (
        resolve_outward_mode(store, "spend", side_effect="spend") == MODE_GATED
    )
