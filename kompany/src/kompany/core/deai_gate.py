"""De-AI / quality pre-flight gate (ADR-0008 #3, step 2).

Scans OUTWARD copy for AI tells / template smell before it ships
unattended. Two layers:

* **RULES layer** (always runs, pure code) — a heuristic scan for the AI
  tells catalogued in the ``de-ai-english`` skill: buzzwords, em-dash
  overuse, "it's not X, it's Y", opener filler, "in today's fast-paced",
  "in conclusion", excessive rule-of-three lists, hedge stacking, etc.
  Each hit is a finding; enough hits → ``'rewrite'``.
* **LLM-judge layer** (``outward=True`` only) — ONE economy-tier LLM call
  that rates the AI-smell of the copy and can escalate the verdict to
  ``'hold'`` (the rules layer never holds on its own; it only flags). The
  call books cost normally (``action_type="deai_gate"``). If the LLM call
  fails the rules verdict stands — the gate NEVER crashes.

Verdicts: ``'pass'`` (ship), ``'rewrite'`` (smells; should be revised but
not blocked), ``'hold'`` (block; park for ``kompany approve``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GateResult:
    """Outcome of a pre-flight quality gate.

    ``verdict`` is ``'pass'`` | ``'rewrite'`` | ``'hold'``. ``findings``
    is a list of human-readable strings describing what tripped the gate.
    """

    verdict: str
    findings: list[str] = field(default_factory=list)
    gate: str = ""

    @property
    def is_hold(self) -> bool:
        return self.verdict == "hold"


# Verdict tokens (shared vocabulary across the pre-flight gates).
PASS = "pass"
REWRITE = "rewrite"
HOLD = "hold"

# Buzzwords the model overproduces (de-ai-english references taxonomy).
_BUZZWORDS = (
    "leverage", "unlock", "delve", "robust", "seamless", "game-changer",
    "game changer", "supercharge", "harness", "elevate", "streamline",
    "empower", "foster", "tapestry", "realm", "landscape", "navigating the",
    "navigate the", "ever-evolving", "ever-changing", "cutting-edge",
)

# Canned opener / filler phrases.
_FILLER_PHRASES = (
    "in today's fast-paced", "in today's fast paced", "in the age of ai",
    "it's worth noting", "it is worth noting", "in conclusion",
    "at the end of the day", "needless to say", "let's be real",
    "here's the thing", "the truth is", "let's dive in", "let's dive into",
)

# Hedge-stack fragments ("can potentially", "may sometimes", ...).
_HEDGE_PHRASES = (
    "can potentially", "may potentially", "may sometimes",
    "tends to often", "might possibly", "could potentially",
)

# "It's not X, it's Y" / negation-pivot family.
_NEGATION_PIVOT = re.compile(
    r"\b(it'?s|this is|that'?s|it is)\s+not\s+(just\s+)?[^,.;]+,\s*(it'?s|but)\b",
    re.IGNORECASE,
)

# Empty intensifiers.
_INTENSIFIERS = ("truly", "incredibly", "remarkably", "deeply", "absolutely")

# Thresholds: how many findings tip the rules verdict from pass → rewrite.
_REWRITE_THRESHOLD = 2
# Em-dashes beyond this in one piece = overuse (false-weight tell).
_EM_DASH_LIMIT = 2
# Rule-of-three lists ("a, b, and c") beyond this = listicle reflex.
_TRIAD_LIMIT = 1

_TRIAD = re.compile(r"\b[\w'-]+,\s+[\w'-]+,\s+and\s+[\w'-]+", re.IGNORECASE)


class DeAIGate:
    """Two-layer de-AI gate. Construct with the engine for the LLM layer.

    ``engine`` may be ``None`` for a pure rules-only gate (e.g. unit
    tests of the heuristics, or internal copy where ``outward=False``).
    """

    def __init__(self, engine: Any | None = None):
        self._engine = engine

    def check(self, text: str, *, outward: bool = True) -> GateResult:
        """Run the rules layer, then (if outward) the LLM-judge layer."""
        findings = self._scan_rules(text or "")
        verdict = REWRITE if len(findings) >= _REWRITE_THRESHOLD else PASS

        if not outward or self._engine is None:
            return GateResult(verdict=verdict, findings=findings, gate="deai")

        judge = self._llm_judge(text or "", findings)
        if judge is not None:
            j_verdict, j_findings = judge
            findings = findings + j_findings
            verdict = _max_verdict(verdict, j_verdict)
        return GateResult(verdict=verdict, findings=findings, gate="deai")

    # ------------------------------------------------------------------
    # Rules layer (pure code)
    # ------------------------------------------------------------------

    def _scan_rules(self, text: str) -> list[str]:
        findings: list[str] = []
        lower = text.lower()

        for word in _BUZZWORDS:
            if word in lower:
                findings.append(f"AI buzzword: '{word}'")
        for phrase in _FILLER_PHRASES:
            if phrase in lower:
                findings.append(f"canned filler: '{phrase}'")
        for phrase in _HEDGE_PHRASES:
            if phrase in lower:
                findings.append(f"hedge stack: '{phrase}'")
        for word in _INTENSIFIERS:
            if re.search(rf"\b{re.escape(word)}\b", lower):
                findings.append(f"empty intensifier: '{word}'")

        if _NEGATION_PIVOT.search(text):
            findings.append("'it's not X, it's Y' negation pivot")

        em_dashes = text.count("—") + text.count(" -- ")
        if em_dashes > _EM_DASH_LIMIT:
            findings.append(
                f"em-dash overuse ({em_dashes} for false weight)"
            )

        triads = len(_TRIAD.findall(text))
        if triads > _TRIAD_LIMIT:
            findings.append(
                f"rule-of-three / listicle reflex ({triads} triadic lists)"
            )

        return findings

    # ------------------------------------------------------------------
    # LLM-judge layer (outward only; defensive fallback)
    # ------------------------------------------------------------------

    def _llm_judge(
        self, text: str, rule_findings: list[str]
    ) -> tuple[str, list[str]] | None:
        """One economy-tier call rating AI-smell. ``None`` on any failure."""
        try:
            settings = self._engine.settings
            resp = self._engine.llm.call(
                model=settings.get_model_for_tier("economy"),
                system=(
                    "You are an adversarial editor judging whether OUTWARD "
                    "copy reads as AI-generated (template smell, no author "
                    "voice, no concrete anchor). Be strict but fair."
                ),
                prompt=(
                    "Copy to judge:\n---\n"
                    f"{(text or '')[:2000]}\n---\n\n"
                    "List concrete AI tells (one per line starting '- '); "
                    "write 'No tells.' if clean. Then on the FINAL line write "
                    "exactly one verdict token: 'PASS' (ships as-is), "
                    "'REWRITE' (smells, should be revised), or 'HOLD' (must "
                    "not ship until rewritten)."
                ),
                agent_name="DeAIGate",
                action_type="deai_gate",
                max_tokens=400,
            )
            return _parse_judge(getattr(resp, "text", "") or "")
        except Exception:  # noqa: BLE001 — LLM miss → rules verdict stands
            return None


def _parse_judge(text: str) -> tuple[str, list[str]]:
    """Extract (verdict, findings) from the judge reply."""
    upper = text.upper()
    if HOLD.upper() in upper:
        verdict = HOLD
    elif REWRITE.upper() in upper:
        verdict = REWRITE
    else:
        verdict = PASS
    findings: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(("- ", "* ", "• ")):
            body = line[2:].strip()
            if body and body.lower().rstrip(".") != "no tells":
                findings.append(f"LLM-judge: {body}")
    return verdict, findings


def _max_verdict(a: str, b: str) -> str:
    """Most-restrictive of two verdicts (hold > rewrite > pass)."""
    order = {PASS: 0, REWRITE: 1, HOLD: 2}
    return a if order.get(a, 0) >= order.get(b, 0) else b


__all__ = ["DeAIGate", "GateResult", "PASS", "REWRITE", "HOLD"]
