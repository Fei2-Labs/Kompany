"""Anima persona layer tests (06-12-anima-persona).

No real LLM anywhere: a FakeLLM captures call kwargs and returns a
canned response; the clock is frozen by monkeypatching
``kompany.core.anima._utcnow`` (subscription_fee precedent). Engine
wiring / suspend-gate tests use the real ``KompanyEngine`` against a
throwaway data dir (conftest isolation).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import kompany.core.anima as anima_mod
from kompany.core.anima import (
    Anima,
    anima_diary_list_op,
    anima_state_op,
    derive_tone,
)
from kompany.state.anima_diary import AnimaDiaryStore
from kompany.state.anima_state import AnimaStateStore
from kompany.state.database import Database
from kompany.state.glossary import GlossaryService

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeLLM:
    def __init__(self, text="Today was a day.", cost_usd=0.0042):
        self.calls: list[dict] = []
        self._text = text
        self._cost = cost_usd

    def call(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text=self._text,
            cost_usd=self._cost,
            input_tokens=10,
            output_tokens=20,
            model=kwargs.get("model"),
        )


class FakeEngine:
    """Minimal engine surface for core/anima.py."""

    def __init__(self, tmp_path):
        self.db = Database(tmp_path / "db")
        self.llm = FakeLLM()
        self.glossary = GlossaryService(self.db)
        self.settings = SimpleNamespace(
            company_name="Testco",
            model_economy="economy-model",
            anima_enabled=True,
            anima_diary_enabled=True,
            get_model_for_tier=lambda tier: "economy-model",
        )


def _backdate_state(db, minutes=10):
    """Push the emotion row's updated_at into the past so freshly seeded
    rows (same-second datetime('now')) fall inside the scan window."""
    AnimaStateStore(db).get()
    db.execute(
        "UPDATE anima_state SET updated_at = "
        f"datetime('now', '-{int(minutes)} minutes') WHERE id = 1"
    )
    db.commit()


def _seed_task(db, tid, status):
    db.execute(
        "INSERT OR IGNORE INTO projects (id, name, type, status) "
        "VALUES ('p1', 'P', 'product', 'active')"
    )
    db.execute(
        "INSERT INTO tasks (id, project_id, title, status, assigned_agent) "
        "VALUES (?, 'p1', ?, ?, 'cto')",
        (tid, f"task {tid}", status),
    )
    db.commit()


def _seed_income(db, amount=100.0):
    db.execute(
        "INSERT INTO ledger (amount, balance_after, description, category) "
        "VALUES (?, ?, 'sale', 'income')",
        (amount, amount),
    )
    db.commit()


def _seed_health_event(db, kind):
    db.execute(
        "INSERT INTO health_events (id, kind) VALUES (?, ?)",
        (f"he-{kind}", kind),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Emotion: event matrix + decay + persistence
# ---------------------------------------------------------------------------


def test_emotion_income_raises_valence(tmp_path):
    engine = FakeEngine(tmp_path)
    _backdate_state(engine.db)
    _seed_income(engine.db)
    actions = Anima(engine).emotion_tick()
    state = AnimaStateStore(engine.db).get()
    assert state["valence"] > 0
    assert "anima_nudge:income:1" in actions


def test_emotion_task_failed_lowers_valence(tmp_path):
    engine = FakeEngine(tmp_path)
    _backdate_state(engine.db)
    _seed_task(engine.db, "t1", "failed")
    actions = Anima(engine).emotion_tick()
    state = AnimaStateStore(engine.db).get()
    assert state["valence"] < 0
    assert "anima_nudge:task_failed:1" in actions


def test_emotion_task_completed_raises_energy(tmp_path):
    engine = FakeEngine(tmp_path)
    _backdate_state(engine.db)
    _seed_task(engine.db, "t1", "completed")
    _seed_task(engine.db, "t2", "delivered")
    actions = Anima(engine).emotion_tick()
    state = AnimaStateStore(engine.db).get()
    assert state["energy"] > 0.5
    assert "anima_nudge:task_completed:2" in actions


@pytest.mark.parametrize(
    "kind",
    ["runway_alert", "self_update_t3_blocked", "harness_vehicle_missing"],
)
def test_emotion_alarm_events_lower_valence(tmp_path, kind):
    engine = FakeEngine(tmp_path)
    _backdate_state(engine.db)
    _seed_health_event(engine.db, kind)
    actions = Anima(engine).emotion_tick()
    state = AnimaStateStore(engine.db).get()
    assert state["valence"] < 0
    assert f"anima_nudge:{kind}:1" in actions


def test_emotion_idle_tick_decays_toward_neutral(tmp_path):
    engine = FakeEngine(tmp_path)
    store = AnimaStateStore(engine.db)
    store.set(valence=1.0, energy=1.0)
    actions = Anima(engine).emotion_tick()
    state = store.get()
    assert state["valence"] == pytest.approx(0.9)  # 10% toward 0
    assert state["energy"] == pytest.approx(0.95)  # 10% toward 0.5
    assert actions == ["anima_emotion"]  # no nudges fired


def test_emotion_state_persists_and_clamps(tmp_path):
    engine = FakeEngine(tmp_path)
    store = AnimaStateStore(engine.db)
    store.set(valence=5.0, energy=-2.0)
    state = store.get()
    assert state["valence"] == 1.0
    assert state["energy"] == 0.0
    # Re-open from disk: same row.
    reread = AnimaStateStore(Database(tmp_path / "db")).get()
    assert reread["valence"] == 1.0


# ---------------------------------------------------------------------------
# Tone derivation
# ---------------------------------------------------------------------------


def test_derive_tone_quadrants():
    assert derive_tone(0.5, 0.8) == "energized and optimistic"
    assert derive_tone(0.5, 0.3) == "tired but satisfied"
    assert derive_tone(-0.5, 0.8) == "worried but determined"
    assert derive_tone(-0.5, 0.3) == "worried, conserving energy"
    assert derive_tone(0.0, 0.5) == "flat, routine"


# ---------------------------------------------------------------------------
# Diary: once per day, tone in prompt, cost recorded, fake clock
# ---------------------------------------------------------------------------


def test_diary_once_per_day_across_ticks(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path)
    anima = Anima(engine)
    now = datetime(2026, 6, 12, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(anima_mod, "_utcnow", lambda: now)

    assert anima.diary_tick() == ["anima_diary:2026-06-12"]
    assert anima.diary_tick() == []
    assert anima.diary_tick() == []
    assert len(engine.llm.calls) == 1

    entries = AnimaDiaryStore(engine.db).list_entries()
    assert len(entries) == 1
    row = entries[0]
    assert row["date"] == "2026-06-12"
    assert row["entry"] == "Today was a day."
    assert row["cost"] == pytest.approx(0.0042)

    # Next calendar day → exactly one more entry.
    now = datetime(2026, 6, 13, 0, 5, tzinfo=UTC)
    monkeypatch.setattr(anima_mod, "_utcnow", lambda: now)
    assert anima.diary_tick() == ["anima_diary:2026-06-13"]
    assert anima.diary_tick() == []
    assert len(AnimaDiaryStore(engine.db).list_entries()) == 2


def test_diary_prompt_carries_tone_and_books_cost_path(tmp_path, monkeypatch):
    engine = FakeEngine(tmp_path)
    AnimaStateStore(engine.db).set(valence=0.8, energy=0.9)
    monkeypatch.setattr(
        anima_mod, "_utcnow",
        lambda: datetime(2026, 6, 12, 9, 0, tzinfo=UTC),
    )
    Anima(engine).diary_tick()
    call = engine.llm.calls[0]
    assert "energized and optimistic" in call["prompt"]
    # Cost path: normal LLM call with the bookkeeping labels + economy tier.
    assert call["action_type"] == "anima_diary"
    assert call["agent_name"] == "Anima"
    assert call["model"] == "economy-model"


# ---------------------------------------------------------------------------
# Glossary seed (D1)
# ---------------------------------------------------------------------------


def test_ensure_glossary_entry_idempotent(tmp_path):
    engine = FakeEngine(tmp_path)
    anima = Anima(engine)
    assert anima.ensure_glossary_entry() is True
    assert anima.ensure_glossary_entry() is False
    entries = [e for e in engine.glossary.list_terms() if e.term == "anima"]
    assert len(entries) == 1
    assert "PROVISIONAL" in entries[0].definition


# ---------------------------------------------------------------------------
# Engine wiring: ticker actions, flags, suspend gate
# ---------------------------------------------------------------------------


def _real_engine(monkeypatch, tmp_path):
    from kompany.core.engine import KompanyEngine

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KOMPANY_TEST_MODE", "1")
    return KompanyEngine()


def test_engine_registers_anima_actions(monkeypatch, tmp_path):
    engine = _real_engine(monkeypatch, tmp_path)
    names = [name for name, _ in engine.ticker.actions]
    assert names[-2:] == ["anima_emotion", "anima_diary"]
    # Glossary seeded at construction.
    assert engine.glossary.get("anima") is not None


def test_engine_anima_disabled_registers_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("KOMPANY_ANIMA_ENABLED", "0")
    engine = _real_engine(monkeypatch, tmp_path)
    names = [name for name, _ in engine.ticker.actions]
    assert "anima_emotion" not in names
    assert "anima_diary" not in names
    assert engine.anima is None


def test_engine_diary_flag_off_keeps_emotion(monkeypatch, tmp_path):
    monkeypatch.setenv("KOMPANY_ANIMA_DIARY_ENABLED", "0")
    engine = _real_engine(monkeypatch, tmp_path)
    names = [name for name, _ in engine.ticker.actions]
    assert "anima_emotion" in names
    assert "anima_diary" not in names


def test_suspended_company_writes_no_diary(monkeypatch, tmp_path):
    engine = _real_engine(monkeypatch, tmp_path)
    engine.llm = FakeLLM()  # belt and braces: even if reached, no real call
    engine.runtime.set("suspended", reason="test")
    row = engine.ticker.tick_once()
    assert row["outcome"] == "idle_suspended"
    assert engine.llm.calls == []
    assert AnimaDiaryStore(engine.db).list_entries() == []


# ---------------------------------------------------------------------------
# Constitution negative test (D2): emotion never reaches C-suite prompts
# ---------------------------------------------------------------------------

ANIMA_WORDS = [
    "valence",
    "anima",
    "energized and optimistic",
    "tired but satisfied",
    "worried but determined",
    "worried, conserving energy",
    "flat, routine",
]


def test_ceo_classify_prompts_carry_no_emotion(tmp_path):
    """CEO.classify has no anima input by construction — and with extreme
    emotion state present, the prompts it builds stay emotion-free."""
    from kompany.agents.ceo import CEOAgent
    from kompany.config.settings import KompanySettings

    db = Database(tmp_path / "db")
    AnimaStateStore(db).set(valence=-1.0, energy=0.0)  # extreme state exists

    captured: dict = {}

    class CapturingLLM:
        def call_structured(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(parsed=None, cost_usd=0.0)

    ceo = CEOAgent(CapturingLLM(), KompanySettings())
    ceo.classify("buy a domain for the landing page")
    blob = (captured["system"] + captured["prompt"]).lower()
    for word in ANIMA_WORDS:
        assert word not in blob


# ---------------------------------------------------------------------------
# Four-interface parity (test_agent_work_summary precedent)
# ---------------------------------------------------------------------------


class _OpsEngine:
    """Minimal engine carrying only what the ops + transports need."""

    def __init__(self, db):
        self.db = db
        self.settings = SimpleNamespace(anima_enabled=True)

    def anima_state(self):
        return anima_state_op(self)

    def anima_diary_list(self, limit: int = 30):
        return anima_diary_list_op(self, limit=limit)

    async def start(self):  # api lifespan hook
        pass

    async def stop(self):
        pass


def test_anima_parity_sdk_rest_mcp(tmp_path, monkeypatch):
    import asyncio
    import json as _json

    from fastapi.testclient import TestClient

    import kompany.interfaces.api as api_mod
    from kompany.interfaces import mcp_proxy, mcp_server

    db = Database(tmp_path / "db")
    AnimaStateStore(db).set(valence=0.4, energy=0.7)
    AnimaDiaryStore(db).record(
        date="2026-06-12", entry="hello", valence=0.4, energy=0.7, cost=0.01
    )
    engine = _OpsEngine(db)

    # SDK path == engine op by construction.
    sdk_state = engine.anima_state()
    sdk_diary = engine.anima_diary_list()

    monkeypatch.setattr(api_mod, "_engine", engine)
    with TestClient(api_mod.app) as client:
        rest_state = client.get("/anima/state").json()
        rest_diary = client.get("/anima/diary").json()

    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)
    monkeypatch.setattr(mcp_server, "_engine", engine)
    out = asyncio.run(mcp_server.call_tool("kompany_anima_state", {}))
    mcp_state = _json.loads(out[0].text)
    out = asyncio.run(mcp_server.call_tool("kompany_anima_diary", {}))
    mcp_diary = _json.loads(out[0].text)

    assert sdk_state == rest_state == mcp_state
    assert sdk_diary == rest_diary == mcp_diary
    assert mcp_state["tone"] == "energized and optimistic"
    assert mcp_diary[0]["entry"] == "hello"
