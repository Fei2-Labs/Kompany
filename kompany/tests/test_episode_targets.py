"""Tests for the ``targets`` slot in ``EpisodePayloadV1`` + materialize.

Mission-targets task 05-19. Verifies:

* ``EpisodePayloadV1`` accepts an optional ``targets`` bundle without
  bumping ``schema_version``.
* ``Episodes.materialize`` populates the slot from
  ``state.targets.get_bundle`` when targets exist.
* Materialize returns ``targets=None`` when nothing has been written.
* The full chain — onboard → review → materialize — carries the three
  states + thread id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kompany.state.database import Database
from kompany.state.episode_payload import (
    EpisodePayloadV1,
    ProjectMeta,
    TargetsBundleEntry,
    TargetsSnapshot,
)
from kompany.state.episodes import Episodes
from kompany.state.targets import (
    CompanyTargets,
    set_review_thread_id,
    set_targets,
)


def test_payload_accepts_targets_slot() -> None:
    payload = EpisodePayloadV1(
        project_meta=ProjectMeta(
            id="proj_1", name="Demo", status="completed", created_at="now"
        ),
        targets=TargetsBundleEntry(
            founder=TargetsSnapshot(
                initial_budget=5000.0,
                revenue_target=10000.0,
                customer_target=50,
                deadline="2026-09-30",
                source="founder",
            ),
            proposal=None,
            agreed=None,
            review_thread_id="apr_xyz",
        ),
    )
    assert payload.schema_version == "1.0"
    assert payload.targets is not None
    assert payload.targets.founder is not None
    assert payload.targets.founder.revenue_target == 10000.0
    assert payload.targets.review_thread_id == "apr_xyz"


def test_payload_targets_default_is_none() -> None:
    payload = EpisodePayloadV1(
        project_meta=ProjectMeta(
            id="proj_1", name="Demo", status="completed", created_at="now"
        ),
    )
    assert payload.targets is None


def _seed_project(db: Database, project_id: str = "proj_1") -> None:
    """Insert a minimal completed project row so materialize has something to chew on."""
    db.execute(
        """INSERT INTO projects (id, name, type, status, plan)
           VALUES (?, ?, 'operational', 'completed', '{}')""",
        (project_id, "Demo project"),
    )
    db.commit()


def test_materialize_collects_targets_when_set(tmp_path: Path) -> None:
    db = Database(tmp_path)
    _seed_project(db)
    set_targets(
        db,
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=10000.0,
            customer_target=50,
            deadline="2099-01-01",
            source="founder",
        ),
    )
    set_targets(
        db,
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=7000.0,
            source="team_proposal",
        ),
    )
    set_targets(
        db,
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=7500.0,
            source="agreed",
        ),
    )
    set_review_thread_id(db, "apr_xyz")
    episodes = Episodes(db)
    payload = episodes.materialize("proj_1")
    assert payload.targets is not None
    assert payload.targets.founder is not None
    assert payload.targets.founder.revenue_target == 10000.0
    assert payload.targets.proposal is not None
    assert payload.targets.proposal.revenue_target == 7000.0
    assert payload.targets.agreed is not None
    assert payload.targets.agreed.revenue_target == 7500.0
    assert payload.targets.review_thread_id == "apr_xyz"


def test_materialize_returns_none_targets_when_unset(tmp_path: Path) -> None:
    db = Database(tmp_path)
    _seed_project(db)
    episodes = Episodes(db)
    payload = episodes.materialize("proj_1")
    assert payload.targets is None


def test_materialize_serialises_targets_in_payload_json(tmp_path: Path) -> None:
    db = Database(tmp_path)
    _seed_project(db)
    set_targets(
        db,
        CompanyTargets(
            initial_budget=5000.0,
            revenue_target=10000.0,
            source="founder",
        ),
    )
    episodes = Episodes(db)
    row = episodes.record_or_update("proj_1")
    import json

    parsed = json.loads(row["payload_json"])
    assert parsed["schema_version"] == "1.0"
    assert "targets" in parsed
    assert parsed["targets"]["founder"]["revenue_target"] == 10000.0


# ---------------------------------------------------------------------------
# Full chain — onboard → directive → review → materialize
# ---------------------------------------------------------------------------


def test_full_chain_onboard_review_then_materialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: apply template, run review, approve, materialize the
    template-spawned draft project — verify the payload carries the
    founder / proposal / agreed trio."""
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    from kompany.core.engine import KompanyEngine

    engine = KompanyEngine()
    engine.apply_template(
        "saas-startup",
        override_budget=1000.0,
        override_revenue_target=10000.0,
        override_deadline="2099-01-01",
    )
    out = engine.run_target_feasibility_review(skip_llm=True)
    assert out is not None
    engine.approve_request(out["id"], approved_by="master")

    # Find one of the draft projects the template created so we can
    # materialize it.
    proj_row = engine.db.execute(
        "SELECT id FROM projects ORDER BY rowid LIMIT 1"
    ).fetchone()
    project_id = proj_row["id"]
    # Flip status to ``completed`` so ``materialize`` produces a normal
    # payload — the contract works on any row, but we want it
    # representative.
    engine.db.execute(
        "UPDATE projects SET status = 'completed' WHERE id = ?", (project_id,)
    )
    engine.db.commit()
    payload = engine.episodes.materialize(project_id)
    assert payload.targets is not None
    assert payload.targets.founder is not None
    assert payload.targets.founder.revenue_target == 10000.0
    assert payload.targets.proposal is not None
    assert payload.targets.agreed is not None
    assert payload.targets.review_thread_id == out["id"]
