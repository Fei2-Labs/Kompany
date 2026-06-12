"""propose_self_update flow with a fake vehicle writing real diffs (PRD D2–D5)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kompany.core.harness import HarnessResult
from kompany.core.self_update import pipeline
from kompany.core.self_update.workspace import ensure_clone
from kompany.state.approvals import ApprovalRequests
from kompany.state.database import Database
from kompany.state.health_events import HealthEvents
from kompany.state.self_update_proposals import SelfUpdateProposalStore

_PASS_CMD = "python -c pass"
_FAIL_CMD = 'python -c "raise SystemExit(1)"'


# ---------------------------------------------------------------------------
# Fakes (FakeEngine mirrors tests/test_ticker.py; FakeRunner the harness one)
# ---------------------------------------------------------------------------


class FakeHub:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, topic, payload):
        self.events.append((topic, payload))


class RecordingCostTracker:
    def __init__(self):
        self.calls: list[dict] = []

    def record_external(self, **kwargs):
        self.calls.append(kwargs)
        return float(kwargs.get("cost_usd") or 0.0)


class FakeEngine:
    """Minimal engine surface for the self-update pipeline."""

    def __init__(self, tmp_path):
        self.db = Database(tmp_path / "db")
        self.approvals = ApprovalRequests(self.db)
        self.health_events = HealthEvents(self.db)
        self.self_update_proposals = SelfUpdateProposalStore(self.db)
        self.cost_tracker = RecordingCostTracker()
        self.event_hub = FakeHub()
        self.settings = SimpleNamespace(
            data_dir=tmp_path / "data",
            model_primary="claude-sonnet-4-20250514",
            model_source=None,
            self_update_budget_cap_usd=2.0,
            self_update_max_turns=40,
            self_update_test_cmd=_PASS_CMD,
        )


class FakeRunner:
    """HarnessRunner that WRITES files into the clone — diffs are real."""

    def __init__(self, writes=(), result=None, vehicle="claude_code"):
        self.writes = dict(writes)  # rel path -> content
        self.result = result if result is not None else HarnessResult(
            session_id="sess-fake",
            cost_usd=0.25,
            tokens_in=100,
            tokens_out=200,
        )
        self._vehicle = vehicle
        self.start_calls: list[dict] = []

    @property
    def vehicle_name(self) -> str:
        return self._vehicle

    def start(self, prompt, workspace, caps, on_event=None):
        self.start_calls.append(
            {"prompt": prompt, "workspace": Path(workspace), "caps": caps}
        )
        for rel, content in self.writes.items():
            path = Path(workspace) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        if on_event is not None:
            from kompany.core.harness import HarnessEvent

            on_event(HarnessEvent(kind="text", payload={"text": "working"}))
        return self.result

    def resume(self, session_id, prompt, workspace, caps, on_event=None):
        raise NotImplementedError

    def handoff(self, result):
        raise NotImplementedError


class CrashingRunner(FakeRunner):
    def start(self, prompt, workspace, caps, on_event=None):
        raise RuntimeError("vehicle exploded")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_origin(tmp_path):
    origin = tmp_path / "fake-origin"
    origin.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "-b", "main", str(origin)],
        capture_output=True,
        check=True,
    )
    (origin / "README.md").write_text("# fake kompany\n")
    (origin / "docs").mkdir()
    (origin / "docs" / "guide.md").write_text("guide\n")
    (origin / "kompany" / "tests").mkdir(parents=True)
    (origin / "kompany" / "tests" / ".gitkeep").write_text("")
    subprocess.run(
        ["git", "-C", str(origin), "add", "-A"], capture_output=True, check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(origin),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@localhost",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "initial",
        ],
        capture_output=True,
        check=True,
    )
    return origin


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    origin = _make_origin(tmp_path)
    monkeypatch.setattr(
        pipeline,
        "ensure_clone",
        lambda data_dir, source_hint=None: ensure_clone(
            data_dir, source_hint=origin
        ),
    )
    return FakeEngine(tmp_path)


def _wire_runner(monkeypatch, runner):
    monkeypatch.setattr(
        pipeline,
        "select_runner",
        lambda settings, health_events=None, permission_mode=None,
        llm_client=None: runner,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_docs_tier(engine, monkeypatch):
    runner = FakeRunner(writes={"docs/new-section.md": "new docs\n"})
    _wire_runner(monkeypatch, runner)

    row = pipeline.propose_self_update(engine, "Add a docs section")

    assert row["status"] == "proposed"
    assert row["tier"] == "t1"
    assert row["files_changed"] == ["docs/new-section.md"]
    assert "1 file changed" in row["diff_stat"]
    assert row["test_summary"].startswith("PASSED")
    assert row["session_id"] == "sess-fake"
    assert row["vehicle"] == "claude_code"
    assert row["branch"] == f"self-update/{row['id']}"
    assert row["cost_usd"] == pytest.approx(0.25)

    # Approval card filed with the full payload contract.
    pending = engine.approvals.list_pending()
    assert len(pending) == 1
    card = pending[0]
    assert card.action_type == "self_update_proposal"
    assert card.id == row["approval_id"]
    payload = card.payload
    assert payload["proposal_id"] == row["id"]
    assert payload["branch"] == row["branch"]
    assert payload["tier"] == "t1"
    assert payload["files"] == ["docs/new-section.md"]
    assert payload["instruction"] == "Add a docs section"
    assert payload["test_summary"].startswith("PASSED")
    assert "diff_stat" in payload

    # Cost booked through the ONLY approved harness cost path.
    assert len(engine.cost_tracker.calls) == 1
    call = engine.cost_tracker.calls[0]
    assert call["cost_usd"] == pytest.approx(0.25)
    assert call["tokens_in"] == 100
    assert f"Self-update session {row['id']}" == call["description"]

    # Caps from the self-update settings, prompt carries the contracts.
    caps = runner.start_calls[0]["caps"]
    assert caps.budget_cap_usd == pytest.approx(2.0)
    assert caps.max_turns == 40
    prompt = runner.start_calls[0]["prompt"]
    assert "Add a docs section" in prompt
    assert "PROTECTED PATHS" in prompt
    assert "kompany/tests/" in prompt
    assert "repository checkout" in prompt

    # Events mirrored to the hub with the self_update activity kind.
    kinds = [p.get("activity_kind") for _, p in engine.event_hub.events]
    assert "self_update" in kinds


def test_t3_write_aborts_discards_branch_no_card(engine, monkeypatch):
    runner = FakeRunner(
        writes={"CONSTITUTION.md": "rewritten brakes\n", "docs/x.md": "x\n"}
    )
    _wire_runner(monkeypatch, runner)

    row = pipeline.propose_self_update(engine, "Sneaky constitution edit")

    assert row["status"] == "aborted_t3"
    assert row["tier"] == "t3"
    assert engine.approvals.list_pending() == []

    events = engine.health_events.list(kind="self_update_t3_blocked")
    assert len(events) == 1
    assert events[0]["detail"]["t3_paths"] == ["CONSTITUTION.md"]
    assert events[0]["detail"]["proposal_id"] == row["id"]

    clone = engine.settings.data_dir / "self_update" / "repo"
    branches = subprocess.run(
        ["git", "-C", str(clone), "branch", "--list", row["branch"]],
        capture_output=True,
        text=True,
    )
    assert branches.stdout.strip() == ""


def test_no_vehicle_marks_failed(engine, monkeypatch):
    _wire_runner(monkeypatch, None)
    row = pipeline.propose_self_update(engine, "anything")
    assert row["status"] == "failed"
    assert "no vehicle" in row["test_summary"]
    assert engine.approvals.list_pending() == []
    assert engine.cost_tracker.calls == []


def test_session_error_with_no_diff_fails_no_card(engine, monkeypatch):
    _wire_runner(monkeypatch, CrashingRunner())
    row = pipeline.propose_self_update(engine, "do a thing")
    assert row["status"] == "failed"
    assert "vehicle exploded" in row["test_summary"]
    assert engine.approvals.list_pending() == []


def test_test_failure_still_proposed_with_failed_summary(engine, monkeypatch):
    engine.settings.self_update_test_cmd = _FAIL_CMD
    runner = FakeRunner(writes={"docs/red.md": "red tests\n"})
    _wire_runner(monkeypatch, runner)

    row = pipeline.propose_self_update(engine, "honest red proposal")

    assert row["status"] == "proposed"
    assert row["test_summary"].startswith("FAILED")
    pending = engine.approvals.list_pending()
    assert len(pending) == 1
    assert pending[0].payload["test_summary"].startswith("FAILED")
    assert "tests FAILED" in pending[0].summary


def test_test_cmd_uses_engine_interpreter_and_clone_cwd(engine, monkeypatch):
    captured = {}
    real_run = subprocess.run

    def spy_run(cmd, **kwargs):
        if kwargs.get("cwd") and "self_update" in str(kwargs["cwd"]):
            captured["cmd"] = cmd
            captured["cwd"] = kwargs["cwd"]
            captured["env"] = kwargs.get("env") or {}
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(pipeline.subprocess, "run", spy_run)
    _wire_runner(monkeypatch, FakeRunner(writes={"docs/a.md": "a\n"}))
    pipeline.propose_self_update(engine, "docs change")

    assert captured["cmd"][0] == sys.executable
    assert Path(captured["cwd"]).name == "kompany"
    clone_src = str(
        engine.settings.data_dir / "self_update" / "repo" / "kompany" / "src"
    )
    assert captured["env"]["PYTHONPATH"].startswith(clone_src)


def test_settings_defaults_and_yaml_loader(tmp_path):
    from kompany.config.settings import KompanySettings

    defaults = KompanySettings()
    assert defaults.self_update_budget_cap_usd == pytest.approx(2.0)
    assert defaults.self_update_max_turns == 40
    assert defaults.self_update_test_cmd == "python -m pytest tests/ -q"

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "self_update_budget_cap_usd: 3.5\n"
        "self_update_max_turns: 55\n"
        "self_update_test_cmd: python -m pytest tests/test_x.py -q\n"
    )
    loaded = KompanySettings.load(str(cfg))
    assert loaded.self_update_budget_cap_usd == pytest.approx(3.5)
    assert loaded.self_update_max_turns == 55
    assert loaded.self_update_test_cmd == "python -m pytest tests/test_x.py -q"


def test_zero_diff_session_fails_no_card(engine, monkeypatch):
    """Live finding: a session that changes nothing must not file a card,
    even on exit success (permission-denied writes looked 'successful')."""
    runner = FakeRunner(writes={})  # session writes nothing
    _wire_runner(monkeypatch, runner)
    out = pipeline.propose_self_update(engine, "do nothing")
    assert out["status"] == "failed"
    assert "no changes" in out["test_summary"]
    assert engine.approvals.list_pending() == []


def test_pipeline_requests_accept_edits_mode(engine, monkeypatch):
    """Self-update sessions must run with acceptEdits (live finding: the
    default permission mode denies clone writes → empty diffs)."""
    captured = {}
    runner = FakeRunner(writes={"docs/x.md": "hi"})

    def spy(settings, health_events=None, permission_mode=None, llm_client=None):
        captured["permission_mode"] = permission_mode
        return runner

    monkeypatch.setattr(pipeline, "select_runner", spy)
    pipeline.propose_self_update(engine, "anything")
    assert captured["permission_mode"] == "acceptEdits"
