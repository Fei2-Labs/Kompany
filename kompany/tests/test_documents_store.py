"""ProjectDocumentStore — versioned, approved-immutable project documents."""

from __future__ import annotations

from pathlib import Path

import pytest

from kompany.state.database import Database
from kompany.state.documents import (
    DocumentImmutable,
    DocumentStatus,
    IllegalDocumentTransition,
    ProjectDocumentStore,
    content_checksum,
)


@pytest.fixture()
def store(tmp_path: Path) -> ProjectDocumentStore:
    return ProjectDocumentStore(Database(tmp_path / "db"))


def test_draft_assigns_incrementing_versions_per_scope(store):
    a = store.draft("branding.strategy", "main", {"x": 1}, project_id="p1")
    b = store.draft("branding.strategy", "main", {"x": 2}, project_id="p1")
    other = store.draft("branding.strategy", "main", {"x": 3}, project_id="p2")
    assert (a.version, b.version, other.version) == (1, 2, 1)
    assert a.status is DocumentStatus.DRAFT
    assert a.checksum == content_checksum({"x": 1})


def test_lifecycle_draft_propose_approve_supersedes_previous(store):
    v1 = store.draft("ns", "k", {"v": 1})
    v1 = store.propose(v1.id, approval_id="ap-1")
    assert v1.status is DocumentStatus.PROPOSED and v1.approval_id == "ap-1"
    v1 = store.approve(v1.id, approved_by="master")
    assert v1.status is DocumentStatus.APPROVED
    assert v1.approved_at is not None and v1.approved_by == "master"

    v2 = store.draft("ns", "k", {"v": 2})
    assert v2.predecessor_version == 1  # linked to the approved one
    store.approve(v2.id)
    assert store.get(v1.id).status is DocumentStatus.SUPERSEDED
    assert store.latest_approved("ns", "k").version == 2
    assert [d.version for d in store.list_versions("ns", "k")] == [1, 2]


def test_approved_content_is_immutable(store):
    doc = store.approve(store.draft("ns", "k", {"v": 1}).id)
    with pytest.raises(DocumentImmutable):
        store.update_draft(doc.id, {"v": "rewritten"})
    assert store.get(doc.id).content == {"v": 1}


def test_draft_can_be_updated_until_proposed(store):
    doc = store.draft("ns", "k", {"v": 1})
    doc = store.update_draft(doc.id, {"v": 2})
    assert doc.content == {"v": 2}
    store.propose(doc.id)
    with pytest.raises(DocumentImmutable):
        store.update_draft(doc.id, {"v": 3})


def test_illegal_transitions_raise(store):
    doc = store.draft("ns", "k", {})
    store.reject(doc.id, reason="no")
    assert store.get(doc.id).rejection_reason == "no"
    with pytest.raises(IllegalDocumentTransition):
        store.approve(doc.id)
    with pytest.raises(IllegalDocumentTransition):
        store.propose(doc.id)
    fresh = store.draft("ns", "k", {})
    with pytest.raises(IllegalDocumentTransition):
        store.mark_stale(fresh.id)  # only approved rows go stale


def test_stale_keeps_content_and_counts_as_current_when_asked(store):
    doc = store.approve(store.draft("ns", "k", {"v": 1}).id)
    stale = store.mark_stale(doc.id, note="upstream changed")
    assert stale.status is DocumentStatus.STALE and stale.content == {"v": 1}
    assert store.latest_approved("ns", "k") is None
    assert store.latest_approved("ns", "k", include_stale=True).id == doc.id


def test_latest_and_namespace_listing_filters(store):
    store.approve(store.draft("branding.strategy", "main", {"v": 1}).id)
    store.draft("branding.strategy", "main", {"v": 2})
    store.draft("branding.voice", "main", {"tone": "warm"})
    assert store.latest("branding.strategy", "main").version == 2
    assert store.latest("branding.strategy", "main", status="approved").version == 1
    assert store.get_version("branding.strategy", "main", 1).content == {"v": 1}
    everything = store.list_namespace("branding.")
    assert {d.namespace for d in everything} == {"branding.strategy", "branding.voice"}
    drafts = store.list_namespace("branding.", status=DocumentStatus.DRAFT)
    assert len(drafts) == 2


def test_missing_document_raises_lookup_error(store):
    with pytest.raises(LookupError):
        store.approve("nope")
    assert store.get("nope") is None


def test_set_approval_links_open_versions_only(store):
    doc = store.propose(store.draft("ns", "k", {}).id)
    assert store.set_approval(doc.id, "ap-7").approval_id == "ap-7"
    store.approve(doc.id)
    with pytest.raises(DocumentImmutable):
        store.set_approval(doc.id, "ap-8")
