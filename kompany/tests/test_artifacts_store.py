"""ArtifactStore — provenance registry + JSON-path dependency invalidation."""

from __future__ import annotations

from pathlib import Path

import pytest

from kompany.state.artifacts import ArtifactStatus, ArtifactStore, changed_json_paths
from kompany.state.database import Database
from kompany.state.documents import ProjectDocumentStore


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "db")


def test_register_records_provenance_and_dependencies(db):
    docs = ProjectDocumentStore(db)
    arts = ArtifactStore(db)
    lock = docs.approve(docs.draft("branding.brand_lock", "main", {"colors": {"p": "#f00"}}).id)
    art = arts.register(
        "file:///out/logo.png",
        mime_type="image/png",
        kind="branding.logo",
        metadata={"provider": "fake", "cost_usd": 0.02},
        project_id="p1",
        approval_id="ap-9",
        dependencies=[(lock.id, "$.colors.p")],
    )
    assert art.status is ArtifactStatus.ACTIVE
    assert art.metadata["provider"] == "fake" and art.approval_id == "ap-9"
    deps = arts.dependencies(art.id)
    assert [(d.document_id, d.json_path) for d in deps] == [(lock.id, "$.colors.p")]
    assert [a.id for a in arts.list(project_id="p1", kind="branding.logo")] == [art.id]


def test_dependents_match_changed_paths_prefix_both_ways(db):
    docs = ProjectDocumentStore(db)
    arts = ArtifactStore(db)
    v1 = docs.approve(docs.draft("ns", "k", {"colors": {"p": "red", "s": "blue"}, "name": "A"}).id)
    fine = arts.register("f://fine", dependencies=[(v1.id, "$.colors.p")])
    coarse = arts.register("f://coarse", dependencies=[(v1.id, "$.colors")])
    whole = arts.register("f://whole", dependencies=[(v1.id, "$")])
    unrelated = arts.register("f://name", dependencies=[(v1.id, "$.name")])

    changed = changed_json_paths(v1.content, {"colors": {"p": "green", "s": "blue"}, "name": "A"})
    assert changed == {"$.colors.p"}
    hit = {a.id for a in arts.dependents(v1.id, changed)}
    assert hit == {fine.id, coarse.id, whole.id}
    assert unrelated.id not in hit
    # No filter = everything depending on the document.
    assert len(arts.dependents(v1.id)) == 4


def test_mark_stale_only_touches_active_and_quarantine_is_sticky(db):
    arts = ArtifactStore(db)
    a = arts.register("f://a")
    q = arts.quarantine(arts.register("f://q").id, reason="rejected direction")
    assert q.status is ArtifactStatus.QUARANTINED and q.status_note == "rejected direction"
    changed = arts.mark_stale([a.id, q.id], note="strategy v2 approved")
    assert changed == [a.id]
    assert arts.get(a.id).status is ArtifactStatus.STALE
    assert arts.get(q.id).status is ArtifactStatus.QUARANTINED
    assert [x.id for x in arts.list(status="stale")] == [a.id]


def test_changed_json_paths_handles_lists_and_added_keys():
    old = {"a": [1, 2], "b": {"c": 1}}
    new = {"a": [1, 3, 4], "b": {"c": 1, "d": 2}}
    assert changed_json_paths(old, new) == {"$.a[1]", "$.a[2]", "$.b.d"}
    assert changed_json_paths(old, old) == set()
    assert changed_json_paths(1, "1") == {"$"}
