"""Autopilot (09-26-autopilot-reports): founder reports, heartbeat change
push, automatic distillation and evolution triggers.

No real LLM: the report narrative path is exercised with a monkeypatched
``engine.llm.call`` (canned text or raising), and Telegram delivery with a
monkeypatched ``urlopen``. Engines use the real ``KompanyEngine`` against
the conftest-isolated data dir.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import kompany.core.autopilot_learning as learning_mod
import kompany.core.founder_report as report_mod
from kompany.config.settings import KompanySettings
from kompany.core.autopilot import STEP_NAMES
from kompany.core.autopilot_learning import distill_tick, evolution_tick
from kompany.core.engine import KompanyEngine
from kompany.core.founder_report import generate_report, report_tick
from kompany.core.heartbeat_push import fingerprint, heartbeat_push
from kompany.notifications import auto_adapter
from kompany.state.founder_reports import FounderReportStore


@pytest.fixture
def engine():
    return KompanyEngine()


def _canned_llm(text="Quiet day. Nothing needs you.", cost=0.001):
    calls: list[dict] = []

    def call(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=text, cost_usd=cost)

    return call, calls


def _seed_project(engine, pid="p-1"):
    engine.db.execute(
        "INSERT INTO projects (id, name, type, status) VALUES (?, ?, 'service', 'active')",
        (pid, "Proj"),
    )
    engine.db.commit()


def _seed_task(engine, tid, role, status, pid="p-1", title="t"):
    engine.db.execute(
        "INSERT INTO tasks (id, project_id, title, status, assigned_agent) "
        "VALUES (?, ?, ?, ?, ?)",
        (tid, pid, title, status, role),
    )
    engine.db.commit()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_engine_registers_autopilot_steps_before_anima(engine):
    names = [name for name, _ in engine.ticker.actions]
    for step in STEP_NAMES:
        assert step in names
    assert names.index("founder_report") < names.index("anima_emotion")
    assert isinstance(engine.founder_reports, FounderReportStore)


def test_settings_yaml_loads_autopilot_keys(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "founder_report_cadence: weekly\n"
        "founder_report_delivery: none\n"
        "heartbeat_push_on_change: false\n"
        "distill_min_new_episodes: 3\n"
        "auto_evolution_enabled: false\n"
        "external_judgment_enabled: true\n"
    )
    s = KompanySettings.load(str(cfg))
    assert s.founder_report_cadence == "weekly"
    assert s.founder_report_delivery == "none"
    assert s.heartbeat_push_on_change is False
    assert s.distill_min_new_episodes == 3
    assert s.auto_evolution_enabled is False
    assert s.external_judgment_enabled is True


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def test_generate_report_uses_llm_narrative_and_records_row(engine, monkeypatch):
    call, calls = _canned_llm()
    monkeypatch.setattr(engine.llm, "call", call)
    _seed_project(engine)
    _seed_task(engine, "t-1", "cmo", "completed", title="Launch post")

    row = generate_report(engine, "manual", deliver=False)

    assert row["period"] == "manual"
    assert row["narrative"] == "Quiet day. Nothing needs you."
    assert row["data"]["tasks"] == {"completed": 1}
    assert row["data"]["tasks_done"][0]["title"] == "Launch post"
    assert calls[0]["action_type"] == "founder_report"
    assert engine.founder_reports.latest("manual")["id"] == row["id"]


def test_generate_report_falls_back_when_llm_fails(engine, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("no model")

    monkeypatch.setattr(engine.llm, "call", boom)
    _seed_project(engine)
    _seed_task(engine, "t-1", "cto", "failed", title="Broken build")

    row = generate_report(engine, "daily", deliver=False)

    assert row["narrative"].startswith("Daily report (24h)")
    assert "failed: Broken build (cto)" in row["narrative"]
    assert row["cost"] == 0.0


def test_report_delivery_uses_telegram_when_configured(engine, monkeypatch):
    call, _ = _canned_llm()
    monkeypatch.setattr(engine.llm, "call", call)
    engine.settings.telegram_bot_token = "tok"
    engine.settings.telegram_chat_id = "chat-9"
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "result": {"message_id": 7}}'

    def fake_urlopen(req, timeout=10):
        captured["data"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("kompany.notifications.request.urlopen", fake_urlopen)

    row = generate_report(engine, "daily", deliver=True)

    assert row["delivery"][0]["status"] == "sent"
    assert captured["data"]["chat_id"] == "chat-9"
    assert "Quiet day." in captured["data"]["text"]
    stored = engine.founder_reports.get(row["id"])
    assert stored["delivery"][0]["provider_message_id"] == "7"


def test_report_delivery_off_stores_only(engine, monkeypatch):
    call, _ = _canned_llm()
    monkeypatch.setattr(engine.llm, "call", call)
    engine.settings.founder_report_delivery = "off"
    row = generate_report(engine, "daily", deliver=True)
    assert row["delivery"] == []


def test_auto_adapter_is_dry_run_without_credentials():
    assert auto_adapter(SimpleNamespace(telegram_bot_token="", telegram_chat_id="")) == "dry-run"
    assert auto_adapter(SimpleNamespace(telegram_bot_token="t", telegram_chat_id="c")) == "telegram"


def test_report_tick_is_date_gated_and_weekly_on_monday(engine, monkeypatch):
    call, calls = _canned_llm()
    monkeypatch.setattr(engine.llm, "call", call)
    monday = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
    monkeypatch.setattr(report_mod, "_utcnow", lambda: monday)

    first = report_tick(engine)
    assert sorted(a.split(":")[1] for a in first) == ["daily", "weekly"]
    assert report_tick(engine) == []  # same day → nothing
    assert len(calls) == 2

    tuesday = monday + timedelta(days=1)
    monkeypatch.setattr(report_mod, "_utcnow", lambda: tuesday)
    second = report_tick(engine)
    assert [a.split(":")[1] for a in second] == ["daily"]


def test_report_tick_off_cadence_does_nothing(engine):
    engine.settings.founder_report_cadence = "off"
    assert report_tick(engine) == []


def test_remote_report_command_returns_narrative(engine, monkeypatch):
    call, _ = _canned_llm("Remote narrative.")
    monkeypatch.setattr(engine.llm, "call", call)
    engine.settings.telegram_allowed_chat_ids = "111"
    out = engine.handle_remote_command(
        {"source": "telegram", "chat_id": "111", "text": "/report"}
    )
    assert out["status"] == "executed"
    assert out["message"] == "Remote narrative."


def test_remote_help_lists_report(engine):
    engine.settings.telegram_allowed_chat_ids = "111"
    out = engine.handle_remote_command(
        {"source": "telegram", "chat_id": "111", "text": "help"}
    )
    assert "report" in out["result"]["commands"]


# ---------------------------------------------------------------------------
# Heartbeat change push
# ---------------------------------------------------------------------------


def _payload(state="running", approvals=()):
    notes = []
    if approvals:
        notes.append({
            "kind": "pending_approvals",
            "severity": "action_required",
            "summary": f"{len(approvals)} approval request(s) awaiting user decision.",
            "payload": {"approval_ids": list(approvals)},
        })
    if state == "suspended":
        notes.append({
            "kind": "runtime_suspended",
            "severity": "warning",
            "summary": "suspended",
            "payload": {},
        })
    return {"runtime": {"state": state}, "notifications": notes}


def test_fingerprint_ignores_approval_order():
    assert fingerprint(_payload(approvals=("a", "b"))) == fingerprint(_payload(approvals=("b", "a")))
    assert fingerprint(_payload(approvals=("a",))) != fingerprint(_payload(approvals=("a", "b")))


def test_heartbeat_push_only_on_change(engine, monkeypatch):
    sent: list[list[dict]] = []
    monkeypatch.setattr(
        engine, "dispatch_notifications",
        lambda events, adapter="dry-run": (sent.append(events) or [{"status": "dry_run"}] * len(events)),
    )
    assert heartbeat_push(engine, _payload()) == ["heartbeat_push:quiet"]
    assert heartbeat_push(engine, _payload()) == []
    assert heartbeat_push(engine, _payload(approvals=("a-1",))) == ["heartbeat_push:dry_run"]
    assert heartbeat_push(engine, _payload(approvals=("a-1",))) == []
    assert heartbeat_push(engine, _payload(approvals=("a-1", "a-2"))) == ["heartbeat_push:dry_run"]
    assert heartbeat_push(engine, _payload()) == ["heartbeat_push:quiet"]
    assert len(sent) == 2


def test_heartbeat_push_disabled(engine):
    engine.settings.heartbeat_push_on_change = False
    assert heartbeat_push(engine, _payload(approvals=("a",))) == []


def test_ticker_heartbeat_step_includes_push(engine):
    names = engine.ticker._action_heartbeat()
    assert names[0] == "heartbeat"
    assert any(n.startswith("heartbeat_push:") for n in names)


# ---------------------------------------------------------------------------
# Distillation
# ---------------------------------------------------------------------------


def _seed_episode(engine, pid):
    engine.db.execute(
        "INSERT INTO project_episodes (project_id, summary) VALUES (?, 'ep')", (pid,)
    )
    engine.db.commit()


def test_distill_tick_no_episodes_is_silent(engine, monkeypatch):
    called = []
    monkeypatch.setattr(engine, "distill", lambda **kw: called.append(kw) or {})
    assert distill_tick(engine) == []
    assert called == []


def test_distill_tick_runs_on_first_episode_when_never_run(engine, monkeypatch):
    called = []
    monkeypatch.setattr(engine, "distill", lambda **kw: called.append(kw) or {"written": 1})
    _seed_episode(engine, "p-1")
    assert distill_tick(engine) == ["autopilot_distill:1"]
    assert called[0]["episode_ids"] == ["p-1"]
    assert distill_tick(engine) == []  # checked once per day


def test_distill_tick_waits_for_threshold_within_gap(engine, monkeypatch):
    called = []
    monkeypatch.setattr(engine, "distill", lambda **kw: called.append(kw) or {})
    _seed_episode(engine, "p-1")
    distill_tick(engine)
    assert len(called) == 1
    # Next day: 2 new episodes < threshold 10, last run today → wait.
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr(learning_mod, "_utcnow", lambda: tomorrow)
    engine.db.execute(
        "INSERT INTO project_episodes (project_id, summary, updated_at) VALUES "
        "('p-2', 'ep', datetime('now', '+1 day')), ('p-3', 'ep', datetime('now', '+1 day'))"
    )
    engine.db.commit()
    assert distill_tick(engine) == []
    assert len(called) == 1
    # Eight days later the gap rule fires even with few episodes.
    later = datetime.now(UTC) + timedelta(days=9)
    monkeypatch.setattr(learning_mod, "_utcnow", lambda: later)
    assert distill_tick(engine) == ["autopilot_distill:2"]


def test_distill_tick_disabled(engine, monkeypatch):
    engine.settings.distill_auto_enabled = False
    _seed_episode(engine, "p-1")
    assert distill_tick(engine) == []


def test_distill_failure_is_recorded_not_raised(engine, monkeypatch):
    def boom(**kw):
        raise RuntimeError("cos down")

    monkeypatch.setattr(engine, "distill", boom)
    _seed_episode(engine, "p-1")
    assert distill_tick(engine) == ["autopilot_distill:error"]


# ---------------------------------------------------------------------------
# Evolution triggers
# ---------------------------------------------------------------------------


def _soul(engine, role):
    from kompany.core.artifact_evolution.workspace import ArtifactWorkspace

    ws = ArtifactWorkspace(engine.settings.data_dir)
    ws.ensure()
    (ws.souls / f"{role}.yaml").write_text(f"role: {role}\n")


def test_evolution_tick_proposes_for_repeated_failures_once(engine, monkeypatch):
    engine.settings.auto_evolution_enabled = True
    proposals = []
    monkeypatch.setattr(
        engine, "evolution_propose",
        lambda kind, target, instruction: proposals.append((kind, target, instruction)) or {"id": "ap-1", "status": "applied"},
    )
    _seed_project(engine)
    _seed_task(engine, "t-1", "cmo", "failed", title="Post A")
    _seed_task(engine, "t-2", "cmo", "failed", title="Post B")
    _seed_task(engine, "t-3", "cto", "failed", title="Build")  # below threshold
    _soul(engine, "cmo")

    out = evolution_tick(engine)

    assert out == ["autopilot_evolution:failures:cmo:applied"]
    kind, target, instruction = proposals[0]
    assert (kind, target) == ("soul", "cmo")
    assert "Post A" in instruction and "failed 2 task(s)" in instruction
    # Same day: gated. Next day: deduped for 7 days.
    assert evolution_tick(engine) == []
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr(learning_mod, "_utcnow", lambda: tomorrow)
    assert evolution_tick(engine) == []
    assert len(proposals) == 1


def test_evolution_tick_health_recurrence_targets_cos(engine, monkeypatch):
    engine.settings.auto_evolution_enabled = True
    proposals = []
    monkeypatch.setattr(
        engine, "evolution_propose",
        lambda kind, target, instruction: proposals.append((kind, target, instruction)) or {"id": "ap-2", "status": "applied"},
    )
    for _ in range(3):
        engine.health_events.record(kind="lane_timeout")
    _soul(engine, "cos")

    out = evolution_tick(engine)

    assert "autopilot_evolution:health:lane_timeout:applied" in out
    assert proposals[0][1] == "cos"
    assert "lane_timeout" in proposals[0][2]


def test_evolution_tick_skips_when_soul_missing(engine, monkeypatch):
    engine.settings.auto_evolution_enabled = True
    proposals = []
    monkeypatch.setattr(engine, "evolution_propose", lambda *a: proposals.append(a) or {})
    _seed_project(engine)
    _seed_task(engine, "t-1", "ghost", "failed")
    _seed_task(engine, "t-2", "ghost", "failed")

    out = evolution_tick(engine)

    assert out == ["autopilot_evolution:skip:failures:ghost"]
    assert proposals == []


def test_evolution_tick_disabled(engine, monkeypatch):
    engine.settings.auto_evolution_enabled = False
    proposals = []
    monkeypatch.setattr(engine, "evolution_propose", lambda *a: proposals.append(a) or {})
    _seed_project(engine)
    _seed_task(engine, "t-1", "cmo", "failed")
    _seed_task(engine, "t-2", "cmo", "failed")
    _soul(engine, "cmo")
    assert evolution_tick(engine) == []
    assert proposals == []


# ---------------------------------------------------------------------------
# Surface parity (interfaces.md equivalence rule)
# ---------------------------------------------------------------------------


def test_reports_parity_sdk_rest_mcp(engine, monkeypatch):
    import asyncio

    from fastapi.testclient import TestClient

    import kompany.interfaces.api as api_mod
    from kompany.interfaces import mcp_proxy, mcp_server
    from kompany.interfaces.sdk import Kompany

    call, _ = _canned_llm("Parity narrative.")
    monkeypatch.setattr(engine.llm, "call", call)
    generate_report(engine, "daily", deliver=False)

    sdk = Kompany.__new__(Kompany)
    sdk._engine = engine
    sdk_rows = sdk.reports()

    monkeypatch.setattr(api_mod, "_engine", engine)
    with TestClient(api_mod.app) as client:
        rest_rows = client.get("/reports").json()
        latest = client.get("/reports/latest?period=daily").json()
        assert client.get("/reports/latest?period=weekly").status_code == 404
        assert client.get("/reports/nope").status_code == 404

    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)
    monkeypatch.setattr(mcp_server, "_engine", engine)
    out = asyncio.run(mcp_server.call_tool("kompany_reports", {}))
    mcp_rows = json.loads(out[0].text)

    assert sdk_rows == rest_rows == mcp_rows
    assert latest["narrative"] == "Parity narrative."
    assert mcp_rows[0]["period"] == "daily"
