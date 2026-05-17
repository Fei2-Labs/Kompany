"""Tests for tool authorization policies."""

from __future__ import annotations

import tempfile
from pathlib import Path

from kompany.state.database import Database
from kompany.state.tool_authorization import ToolAuthorizationStore


def _make_store():
    tmp = tempfile.mkdtemp()
    db = Database(Path(tmp))
    return ToolAuthorizationStore(db), db


def test_default_policies_are_seeded():
    store, _ = _make_store()

    policies = store.list()

    assert any(p.agent_role == "coo" and p.tool_name == "project_execution" for p in policies)
    subagent_network = store.get("subagent", "external_network")
    assert subagent_network is not None
    assert subagent_network.allowed is False
    ciso_restore = store.get("ciso", "backup_restore")
    assert ciso_restore is not None
    assert ciso_restore.allowed is True
    assert ciso_restore.requires_approval is True


def test_set_policy_round_trip():
    store, _ = _make_store()

    policy = store.set(
        "researcher",
        "web_search",
        True,
        "Researcher may search public docs.",
        requires_approval=True,
    )

    assert policy.allowed is True
    assert policy.requires_approval is True
    assert policy.reason == "Researcher may search public docs."
    assert store.get("researcher", "web_search") == policy


def test_policy_persists_across_store_instances():
    store, db = _make_store()
    store.set(
        "writer",
        "publish_draft",
        True,
        "Publishing needs user approval.",
        requires_approval=True,
    )

    fresh = ToolAuthorizationStore(db)
    policy = fresh.get("writer", "publish_draft")

    assert policy is not None
    assert policy.allowed is True
    assert policy.requires_approval is True
    assert policy.reason == "Publishing needs user approval."


def test_list_can_filter_by_agent_role():
    store, _ = _make_store()
    store.set("researcher", "web_search", True, "ok")
    store.set("writer", "web_search", False, "no")

    policies = store.list(agent_role="researcher")

    assert {p.agent_role for p in policies} == {"researcher"}
    assert policies[0].tool_name == "web_search"
