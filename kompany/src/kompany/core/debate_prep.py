"""Pre-debate preparation — decision quality scaffolding (ADR-0006).

Before round 1 of a strategic debate, :class:`DebatePrepper` assembles a
:class:`PreDebateContext` that raises the floor on decision quality:

* **frame_challenges** — 2-3 unstated assumptions plus the "empty chair"
  (the option nobody in the room is arguing for), via ONE economy-tier LLM
  call. If the call fails the debate still runs on a pure-code checklist.
* **convene_roster** — which executives should be in the room, derived from
  the ``decision_type`` (pricing → CFO/CRO/CMO, etc.). The engine *unions*
  this into the existing stage debaters; it never drops anyone.
* **key_learnings** — per-debater prior learnings recalled from
  :class:`~kompany.state.memory.AgentMemory`, scoped to the question.
* **relevant_precedents** — best-effort lines from prior logged decisions.

Everything is best-effort and defensive: empty memory, empty episodes, or
an LLM outage degrade gracefully and never crash the debate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kompany.core.directive import DecisionType

if TYPE_CHECKING:
    from kompany.config.settings import KompanySettings
    from kompany.llm.client import LLMClient
    from kompany.state.episodes import Episodes
    from kompany.state.memory import AgentMemory


# Decision type → executive roles to convene. The engine unions these into
# the stage's existing debaters (missing roles that exist for the stage are
# added; nothing is dropped). Keep role codes lowercase to match the
# registry / STAGE_PROFILES vocabulary.
DECISION_TYPE_ROLES: dict[str, list[str]] = {
    DecisionType.GO_TO_MARKET.value: ["cmo", "cro", "cpo"],
    DecisionType.PRICING.value: ["cfo", "cro", "cmo"],
    DecisionType.HIRING.value: ["coo", "cpo", "cto"],
    DecisionType.INFRA.value: ["cto", "csa"],
    DecisionType.LEGAL.value: ["ciso"],
    DecisionType.FUNDRAISING.value: ["cfo", "ceo"],
    DecisionType.PRODUCT_SCOPE.value: ["cpo", "cto"],
    DecisionType.GENERAL.value: [],
}

# Pure-code fallback used when the frame-challenge LLM call is unavailable
# or returns nothing usable. Generic but real: each line names a class of
# unstated assumption the team should test before committing.
_FALLBACK_FRAME_CHALLENGES: list[str] = [
    "What unstated assumption about the market or customer is this "
    "question taking for granted?",
    "What is the 'empty chair' option nobody is arguing for "
    "(do nothing / the opposite / a smaller bet)?",
    "What would have to be true for the obvious answer to be wrong?",
]

# How many candidate decisions to scan when looking for precedents.
_PRECEDENT_SCAN_LIMIT = 50
_PRECEDENT_RETURN_LIMIT = 3


@dataclass
class PreDebateContext:
    """Everything assembled before round 1 of a strategic debate."""

    question: str
    decision_type: str
    frame_challenges: list[str] = field(default_factory=list)
    convene_roster: list[str] = field(default_factory=list)
    # role → formatted "Prior learnings:" block (may be empty string)
    key_learnings: dict[str, str] = field(default_factory=dict)
    relevant_precedents: list[str] = field(default_factory=list)
    # True when frame_challenges came from the pure-code fallback (LLM down).
    used_fallback: bool = False

    def learnings_for(self, role: str) -> str:
        """Return the recalled learnings block for ``role`` ("" if none)."""
        return self.key_learnings.get(role, "")

    def frame_block(self) -> str:
        """Render frame challenges as a prompt-injectable block ("" if none)."""
        if not self.frame_challenges:
            return ""
        lines = [f"- {c}" for c in self.frame_challenges]
        return (
            "Before you answer, pressure-test the framing of the question.\n"
            "Challenge these assumptions / consider these missing options:\n"
            + "\n".join(lines)
        )


class DebatePrepper:
    """Assembles a :class:`PreDebateContext` for a strategic debate."""

    def __init__(
        self,
        llm: "LLMClient",
        settings: "KompanySettings",
        memory: "AgentMemory",
        episodes: "Episodes | None" = None,
    ):
        self._llm = llm
        self._settings = settings
        self._memory = memory
        self._episodes = episodes

    def prepare(
        self,
        question: str,
        directive_id: str | None,
        decision_type: str,
        stage: str,
        debaters: list[str] | None = None,
    ) -> PreDebateContext:
        """Build the pre-debate context.

        ``debaters`` is the list of roles that will actually debate (used to
        scope per-role learning recall). When omitted, learnings are recalled
        for the convened roster instead. Always returns a context — never
        raises — so a knowledge-retrieval miss can't kill the debate.
        """
        dtype = self._normalize_decision_type(decision_type)
        convene = list(DECISION_TYPE_ROLES.get(dtype, []))

        frame_challenges, used_fallback = self._frame_challenges(
            question, directive_id
        )

        recall_roles = list(debaters) if debaters else list(convene)
        key_learnings = self._recall_learnings(recall_roles, question)
        precedents = self._precedents(question)

        return PreDebateContext(
            question=question,
            decision_type=dtype,
            frame_challenges=frame_challenges,
            convene_roster=convene,
            key_learnings=key_learnings,
            relevant_precedents=precedents,
            used_fallback=used_fallback,
        )

    # ------------------------------------------------------------------
    # Frame challenges (one economy-tier LLM call, fallback on failure)
    # ------------------------------------------------------------------

    def _frame_challenges(
        self, question: str, directive_id: str | None
    ) -> tuple[list[str], bool]:
        """Return (challenges, used_fallback). Never raises."""
        try:
            model = self._settings.get_model_for_tier("economy")
            resp = self._llm.call(
                model=model,
                system=(
                    "You are a sharp, neutral decision-quality reviewer for "
                    "an executive team. You do NOT take sides. You surface "
                    "the framing problem nobody named."
                ),
                prompt=(
                    "The executive team is about to debate this question:\n\n"
                    f'"{question}"\n\n'
                    "List 2-3 short, concrete framing challenges: unstated "
                    "assumptions baked into the question, and the 'empty "
                    "chair' option nobody is arguing for. One per line, "
                    "starting each line with '- '. No preamble, no headers, "
                    "no numbering — just the dashed lines."
                ),
                agent_name="DebatePrepper",
                directive_id=directive_id,
                action_type="debate_frame_challenge",
                max_tokens=512,
            )
        except Exception:  # noqa: BLE001 — frame-challenge must never crash debate
            return list(_FALLBACK_FRAME_CHALLENGES), True

        parsed = self._parse_challenge_lines(getattr(resp, "text", "") or "")
        if not parsed:
            return list(_FALLBACK_FRAME_CHALLENGES), True
        return parsed, False

    @staticmethod
    def _parse_challenge_lines(text: str) -> list[str]:
        """Extract dashed/bulleted lines from the LLM response (≤3)."""
        out: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            for marker in ("- ", "* ", "• "):
                if line.startswith(marker):
                    line = line[len(marker):].strip()
                    break
            else:
                # Strip a leading "1. " / "1) " numbering if present.
                head = line.split(" ", 1)[0].rstrip(".)")
                if head.isdigit() and " " in line:
                    line = line.split(" ", 1)[1].strip()
            if line:
                out.append(line)
            if len(out) >= 3:
                break
        return out

    # ------------------------------------------------------------------
    # Per-debater learning recall
    # ------------------------------------------------------------------

    def _recall_learnings(
        self, roles: list[str], question: str
    ) -> dict[str, str]:
        """Recall up to 3 prior learnings per role, scoped to the question."""
        learnings: dict[str, str] = {}
        for role in roles:
            try:
                block = self._memory.recall_text(
                    role, limit=3, query=question
                )
            except Exception:  # noqa: BLE001 — memory miss must not crash debate
                block = ""
            if block:
                learnings[role] = block
        return learnings

    # ------------------------------------------------------------------
    # Precedents (best-effort read of prior logged decisions)
    # ------------------------------------------------------------------

    def _precedents(self, question: str) -> list[str]:
        """Best-effort scan of prior decisions for relevant precedents."""
        db = self._decisions_db()
        if db is None:
            return []
        try:
            rows = db.execute(
                "SELECT raw_input, directive_type, result FROM decisions "
                "ORDER BY timestamp DESC LIMIT ?",
                (_PRECEDENT_SCAN_LIMIT,),
            ).fetchall()
        except Exception:  # noqa: BLE001 — table may not exist / be empty
            return []

        tokens = {t for t in question.lower().split() if len(t) >= 4}
        scored: list[tuple[int, str]] = []
        for row in rows:
            try:
                raw = (row["raw_input"] or "").strip()
                dtype = (row["directive_type"] or "").strip()
            except (TypeError, KeyError, IndexError):
                continue
            if not raw:
                continue
            overlap = sum(1 for t in tokens if t in raw.lower())
            line = raw if len(raw) <= 140 else raw[:137] + "..."
            label = f"[{dtype}] {line}" if dtype else line
            scored.append((overlap, label))

        # Prefer token-overlap matches; fall back to most-recent ordering
        # (the scan was already timestamp DESC, so a stable sort keeps it).
        scored.sort(key=lambda s: s[0], reverse=True)
        return [label for _score, label in scored[:_PRECEDENT_RETURN_LIMIT]]

    def _decisions_db(self) -> Any | None:
        """Return a DB handle that can read the ``decisions`` table."""
        ep = self._episodes
        if ep is None:
            return None
        db = getattr(ep, "db", None)
        return db

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_decision_type(decision_type: str | None) -> str:
        """Coerce any input to a known ``DecisionType`` value (default general)."""
        if not decision_type:
            return DecisionType.GENERAL.value
        value = str(decision_type).strip().lower()
        if value in DECISION_TYPE_ROLES:
            return value
        return DecisionType.GENERAL.value
