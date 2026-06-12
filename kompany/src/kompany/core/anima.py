"""Anima — the company's persona layer above the C-suite (provisional name).

PRD ``06-12-anima-persona``. Two tick intents appended to the engine's
:class:`kompany.core.ticker.Ticker` actions list (D4):

* ``anima_emotion`` — every tick, PURE CODE (no LLM): scan what happened
  since the previous emotion update (income, task completions/failures,
  alarming health events) and nudge the (valence, energy) state, then
  decay 10% of the distance back toward the neutral point (0, 0.5).
* ``anima_diary`` — at most once per calendar day: distill the last 24h
  into a <=200-word first-person entry via ONE economy-tier LLM call
  (``action_type="anima_diary"`` — cost books through the normal path).

CONSTITUTION honest-assessment clause (D2 hard invariant): emotion
shapes Anima's VOICE only. Nothing in this module is ever injected into
C-suite / classification / debate prompts, and nothing outside this
module reads the tone strings. See ``.trellis/spec/agents/anima.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from kompany.state.anima_diary import AnimaDiaryStore
from kompany.state.anima_state import AnimaStateStore

# Emotion nudges (PRD D2 event matrix). Values are deliberately small so
# a single tick never saturates an axis; clamping happens in the store.
NUDGE_INCOME_VALENCE = 0.15
NUDGE_TASK_FAILED_VALENCE = -0.15
NUDGE_RUNWAY_ALERT_VALENCE = -0.2
NUDGE_T3_BLOCKED_VALENCE = -0.1
NUDGE_VEHICLE_MISSING_VALENCE = -0.1
NUDGE_TASK_COMPLETED_ENERGY = 0.1

# Decay rate per tick: 10% of the distance toward the neutral point.
DECAY_RATE = 0.1
NEUTRAL_VALENCE = 0.0
NEUTRAL_ENERGY = 0.5

# Health event kinds that hurt valence (alarm signals).
ALARM_EVENT_NUDGES: dict[str, float] = {
    "runway_alert": NUDGE_RUNWAY_ALERT_VALENCE,
    "self_update_t3_blocked": NUDGE_T3_BLOCKED_VALENCE,
    "harness_vehicle_missing": NUDGE_VEHICLE_MISSING_VALENCE,
}

# Fallback scan window when the state row has no usable updated_at.
DEFAULT_WINDOW_MINUTES = 60

DIARY_MAX_WORDS = 200
DIARY_MAX_TOKENS = 500

GLOSSARY_TERM = "anima"
GLOSSARY_DEFINITION = (
    "the company's persona layer above the C-suite — emotion state, "
    "diary, autonomous intents. PROVISIONAL name; final name pending "
    "founder decision."
)


def _utcnow() -> datetime:
    """Injectable clock — tests freeze time by monkeypatching this."""
    return datetime.now(UTC)


def _sqlite_ts(dt: datetime) -> str:
    """Render a datetime in sqlite's default 'YYYY-MM-DD HH:MM:SS' form."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def derive_tone(valence: float, energy: float) -> str:
    """Map the (valence, energy) quadrant to a short diary style hint."""
    if valence >= 0.15:
        if energy >= 0.6:
            return "energized and optimistic"
        return "tired but satisfied"
    if valence <= -0.15:
        if energy >= 0.6:
            return "worried but determined"
        return "worried, conserving energy"
    return "flat, routine"


class Anima:
    """Persona intents bound to one engine. Stateless between calls.

    Stores are constructed from ``engine.db`` per call so a backup
    restore (which swaps ``engine.db``) needs no re-wiring here.
    """

    def __init__(self, engine: Any):
        self._engine = engine

    @property
    def state_store(self) -> AnimaStateStore:
        return AnimaStateStore(self._engine.db)

    @property
    def diary_store(self) -> AnimaDiaryStore:
        return AnimaDiaryStore(self._engine.db)

    # ------------------------------------------------------------------
    # anima_emotion tick intent (pure code, no LLM)
    # ------------------------------------------------------------------

    def emotion_tick(self) -> list[str]:
        """Nudge (valence, energy) from recent events, then decay."""
        store = self.state_store
        state = store.get()
        since = self._window_start(state.get("updated_at"))
        valence = float(state["valence"])
        energy = float(state["energy"])
        actions: list[str] = []

        income = self._count(
            "SELECT COUNT(*) AS n FROM ledger "
            "WHERE category = 'income' AND amount > 0 "
            "AND replace(timestamp, 'T', ' ') > ?",
            (since,),
        )
        if income:
            valence += NUDGE_INCOME_VALENCE * income
            actions.append(f"anima_nudge:income:{income}")

        failed = self._count(
            "SELECT COUNT(*) AS n FROM tasks WHERE status = 'failed' "
            "AND replace(updated_at, 'T', ' ') > ?",
            (since,),
        )
        if failed:
            valence += NUDGE_TASK_FAILED_VALENCE * failed
            actions.append(f"anima_nudge:task_failed:{failed}")

        completed = self._count(
            "SELECT COUNT(*) AS n FROM tasks "
            "WHERE status IN ('completed', 'delivered') "
            "AND replace(updated_at, 'T', ' ') > ?",
            (since,),
        )
        if completed:
            energy += NUDGE_TASK_COMPLETED_ENERGY * completed
            actions.append(f"anima_nudge:task_completed:{completed}")

        for kind, nudge in ALARM_EVENT_NUDGES.items():
            n = self._count(
                "SELECT COUNT(*) AS n FROM health_events WHERE kind = ? "
                "AND replace(created_at, 'T', ' ') > ?",
                (kind, since),
            )
            if n:
                valence += nudge * n
                actions.append(f"anima_nudge:{kind}:{n}")

        # Decay toward the neutral point (idle company → flat, rested).
        valence += (NEUTRAL_VALENCE - valence) * DECAY_RATE
        energy += (NEUTRAL_ENERGY - energy) * DECAY_RATE
        store.set(valence=valence, energy=energy)
        actions.append("anima_emotion")
        return actions

    # ------------------------------------------------------------------
    # anima_diary tick intent (date-gated, one economy LLM call)
    # ------------------------------------------------------------------

    def diary_tick(self) -> list[str]:
        """Write today's diary entry once, after the day's first tick."""
        store = self.state_store
        state = store.get()
        today = _utcnow().date().isoformat()
        if state.get("last_diary_date") == today:
            return []
        valence = float(state["valence"])
        energy = float(state["energy"])
        tone = derive_tone(valence, energy)
        digest = self._gather_last_24h()
        settings = self._engine.settings
        resp = self._engine.llm.call(
            model=settings.get_model_for_tier("economy"),
            system=(
                "You are Anima, the persona of the company "
                f"{settings.company_name or 'Kompany'} — its voice and "
                "memory, writing a private first-person diary. Be honest "
                "about facts; never invent numbers or events."
            ),
            prompt=(
                f"Write today's diary entry ({today}) in at most "
                f"{DIARY_MAX_WORDS} words, first person, in this emotional "
                f"tone: {tone}.\n\nWhat actually happened in the last 24 "
                f"hours:\n{digest}\n\nDiary entry only — no headers, no "
                "preamble."
            ),
            agent_name="Anima",
            action_type="anima_diary",
            max_tokens=DIARY_MAX_TOKENS,
        )
        self.diary_store.record(
            date=today,
            entry=(resp.text or "").strip(),
            valence=valence,
            energy=energy,
            cost=float(getattr(resp, "cost_usd", 0.0) or 0.0),
        )
        store.set_last_diary_date(today)
        return [f"anima_diary:{today}"]

    # ------------------------------------------------------------------
    # Glossary seed (D1) — idempotent, one cheap read when present
    # ------------------------------------------------------------------

    def ensure_glossary_entry(self) -> bool:
        """Add the provisional ``anima`` glossary term if missing."""
        glossary = self._engine.glossary
        if glossary.get(GLOSSARY_TERM) is not None:
            return False
        glossary.add(
            term=GLOSSARY_TERM,
            definition=GLOSSARY_DEFINITION,
            added_by="template",
        )
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _count(self, sql: str, params: tuple) -> int:
        row = self._engine.db.execute(sql, params).fetchone()
        return int(row["n"] or 0) if row else 0

    def _window_start(self, updated_at: str | None) -> str:
        """Scan-window start: the previous emotion update (or 1h back)."""
        fallback = _sqlite_ts(
            _utcnow() - timedelta(minutes=DEFAULT_WINDOW_MINUTES)
        )
        if not updated_at:
            return fallback
        return str(updated_at).replace("T", " ")[:19]

    def _gather_last_24h(self) -> str:
        """Plain-text digest of the last 24h for the diary prompt."""
        db = self._engine.db
        since = _sqlite_ts(_utcnow() - timedelta(hours=24))
        lines: list[str] = []
        tasks = db.execute(
            "SELECT title, status FROM tasks "
            "WHERE status IN ('completed', 'delivered', 'failed') "
            "AND replace(updated_at, 'T', ' ') > ? "
            "ORDER BY updated_at DESC LIMIT 20",
            (since,),
        ).fetchall()
        for t in tasks:
            lines.append(f"- task {t['status']}: {t['title']}")
        ledger = db.execute(
            "SELECT category, COUNT(*) AS n, SUM(amount) AS total "
            "FROM ledger WHERE replace(timestamp, 'T', ' ') > ? "
            "GROUP BY category",
            (since,),
        ).fetchall()
        for entry in ledger:
            lines.append(
                f"- ledger {entry['category']}: {entry['n']} entries, "
                f"net {float(entry['total'] or 0.0):+.2f}"
            )
        events = db.execute(
            "SELECT kind, COUNT(*) AS n FROM health_events "
            "WHERE replace(created_at, 'T', ' ') > ? GROUP BY kind",
            (since,),
        ).fetchall()
        for event in events:
            lines.append(f"- health event {event['kind']}: x{event['n']}")
        try:
            approvals = db.execute(
                "SELECT status, COUNT(*) AS n FROM approval_requests "
                "WHERE status != 'pending' "
                "AND replace(created_at, 'T', ' ') > ? GROUP BY status",
                (since,),
            ).fetchall()
            for appr in approvals:
                lines.append(
                    f"- approvals {appr['status']}: {appr['n']}"
                )
        except Exception:  # noqa: BLE001 — approvals digest is best-effort
            pass
        return "\n".join(lines) if lines else "- a quiet day; nothing notable."


# ---------------------------------------------------------------------------
# Engine ops (D5) — single logic home for all four interfaces
# ---------------------------------------------------------------------------


def anima_state_op(engine: Any) -> dict[str, Any]:
    """Current persona state + derived tone (read-only; works when disabled)."""
    state = AnimaStateStore(engine.db).get()
    return {
        "valence": float(state["valence"]),
        "energy": float(state["energy"]),
        "tone": derive_tone(float(state["valence"]), float(state["energy"])),
        "last_diary_date": state.get("last_diary_date"),
        "updated_at": state.get("updated_at"),
        "enabled": bool(getattr(engine.settings, "anima_enabled", True)),
    }


def anima_diary_list_op(engine: Any, limit: int = 30) -> list[dict[str, Any]]:
    """Most recent diary entries, newest first."""
    return AnimaDiaryStore(engine.db).list_entries(limit=limit)


__all__ = [
    "Anima",
    "anima_diary_list_op",
    "anima_state_op",
    "derive_tone",
]
