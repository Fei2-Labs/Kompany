"""No-fabrication pre-flight gate (ADR-0008 #3, step 3).

The 2026-06-14 failure mode: the engine confidently shipped a fabricated
"6-month" timeline. This gate verifies that the **hard claims** in outward
copy — numbers, dates, durations, named metrics, superlatives — are
grounded in the provided evidence (company state + project episodes,
``state/episodes.py``). Any hard claim with no supporting evidence →
``'hold'`` with the offending claims listed.

Claim extraction is LLM-assisted when an engine is available (one
economy-tier call, ``action_type="fabrication_check"``); it falls back to
a deterministic regex claim-scan when the LLM is unavailable or fails. The
*grounding* test is pure code either way: a claim is grounded iff its
salient tokens (the number / date / metric word / superlative) appear in
the flattened evidence text. Defensive: the gate never crashes.
"""

from __future__ import annotations

import re
from typing import Any

from kompany.core.deai_gate import HOLD, PASS, GateResult

# Hard-claim regexes. Each match is a candidate claim that must be grounded.
_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?\b")
_DURATION = re.compile(
    r"\b\d+[-\s]?(?:year|month|week|day|hour|quarter|yr|mo)s?\b",
    re.IGNORECASE,
)
_DATE = re.compile(
    r"\b(?:\d{4}|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?|q[1-4])\b",
    re.IGNORECASE,
)
_SUPERLATIVE = re.compile(
    r"\b(?:first|only|best|fastest|largest|leading|number one|no\.?\s*1|"
    r"#1|world'?s|industry'?s|unmatched|unrivaled|guaranteed)\b",
    re.IGNORECASE,
)
# Named metric phrases ("25% growth", "10x return", "$5,000 MRR").
_METRIC = re.compile(
    r"(?:\$\s?\d[\d,]*(?:\.\d+)?\s?[kmb]?|\d+x|\d[\d,]*\s?"
    r"(?:users?|customers?|mrr|arr|revenue|sales|downloads?|signups?))",
    re.IGNORECASE,
)

_CLAIM_PATTERNS = (_DURATION, _METRIC, _SUPERLATIVE, _DATE, _NUMBER)

# Tokens too generic to count as a grounded match on their own.
_TRIVIAL = frozenset({"0", "1", "2", "a", "an", "the", "one", "two"})


class FabricationCheck:
    """Grounds hard claims against company state + episodes evidence."""

    def __init__(self, engine: Any | None = None):
        self._engine = engine

    def check(
        self,
        text: str,
        *,
        company_state: dict[str, Any] | None = None,
        episodes: list[Any] | None = None,
    ) -> GateResult:
        """Hold if any hard claim is ungrounded in the evidence."""
        text = text or ""
        evidence = _flatten_evidence(company_state, episodes)
        claims = self._extract_claims(text)
        ungrounded = [c for c in claims if not _is_grounded(c, evidence)]

        if ungrounded:
            findings = [
                f"unsourced hard claim: '{c}' (not grounded in company "
                "state / episodes)"
                for c in ungrounded
            ]
            return GateResult(
                verdict=HOLD, findings=findings, gate="fabrication"
            )
        return GateResult(verdict=PASS, findings=[], gate="fabrication")

    # ------------------------------------------------------------------
    # Claim extraction (LLM-assisted, regex fallback)
    # ------------------------------------------------------------------

    def _extract_claims(self, text: str) -> list[str]:
        llm = self._llm_extract(text)
        if llm is not None:
            return llm
        return _regex_extract(text)

    def _llm_extract(self, text: str) -> list[str] | None:
        """One economy call to pull hard claims. ``None`` on any failure."""
        if self._engine is None:
            return None
        try:
            settings = self._engine.settings
            resp = self._engine.llm.call(
                model=settings.get_model_for_tier("economy"),
                system=(
                    "You extract HARD, checkable claims from outward copy: "
                    "specific numbers, dates, durations, named metrics, and "
                    "superlatives. Ignore vague qualitative statements."
                ),
                prompt=(
                    "Copy:\n---\n"
                    f"{text[:2000]}\n---\n\n"
                    "List each hard claim on its own line starting with "
                    "'- ', quoting the exact phrase. If there are none, "
                    "write 'No claims.'."
                ),
                agent_name="FabricationCheck",
                action_type="fabrication_check",
                max_tokens=400,
            )
            return _parse_claim_lines(getattr(resp, "text", "") or "")
        except Exception:  # noqa: BLE001 — fall back to the regex scan
            return None


def _parse_claim_lines(text: str) -> list[str]:
    claims: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(("- ", "* ", "• ")):
            body = line[2:].strip().strip("\"'")
            if body and body.lower().rstrip(".") != "no claims":
                claims.append(body)
    return claims


def _regex_extract(text: str) -> list[str]:
    """Deterministic fallback: pull claim-bearing phrases via regex.

    Returns the offending sentence/clause for each hard pattern hit so the
    grounding test has enough context, deduplicated, order-preserving.
    """
    claims: list[str] = []
    seen: set[str] = set()
    for pattern in _CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            phrase = _claim_context(text, match.start(), match.end())
            key = phrase.lower()
            if key not in seen:
                seen.add(key)
                claims.append(phrase)
    return claims


def _claim_context(text: str, start: int, end: int) -> str:
    """Return the matched span widened to its surrounding clause."""
    left = max(
        text.rfind(".", 0, start), text.rfind(",", 0, start),
        text.rfind("\n", 0, start),
    )
    right_candidates = [
        i for i in (
            text.find(".", end), text.find(",", end), text.find("\n", end)
        ) if i != -1
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1:right].strip()


# ---------------------------------------------------------------------------
# Grounding (pure code)
# ---------------------------------------------------------------------------


def _flatten_evidence(
    company_state: dict[str, Any] | None, episodes: list[Any] | None
) -> str:
    """Flatten the evidence sources into one lowercased searchable string."""
    parts: list[str] = []
    if company_state:
        parts.append(_stringify(company_state))
    for ep in episodes or []:
        parts.append(_stringify(ep))
    return " ".join(parts).lower()


def _stringify(obj: Any) -> str:
    if isinstance(obj, dict):
        return " ".join(f"{k} {_stringify(v)}" for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return " ".join(_stringify(x) for x in obj)
    return str(obj)


def _is_grounded(claim: str, evidence: str) -> bool:
    """A claim is grounded iff its salient hard tokens appear in evidence.

    Salient tokens = the numbers / durations / superlative words inside the
    claim. Every salient token must be present in the evidence; a claim
    with no salient token at all is treated as soft (grounded by default).
    """
    salient = _salient_tokens(claim)
    if not salient:
        return True
    return all(_token_in_evidence(tok, evidence) for tok in salient)


def _salient_tokens(claim: str) -> list[str]:
    tokens: list[str] = []
    for pattern in (_DURATION, _METRIC, _NUMBER, _SUPERLATIVE):
        for m in pattern.finditer(claim):
            tok = m.group(0).strip().lower()
            if tok and tok not in _TRIVIAL and tok not in tokens:
                tokens.append(tok)
    return tokens


def _token_in_evidence(token: str, evidence: str) -> bool:
    """Check a salient token against evidence, number-aware.

    For a token that contains digits, the digit run must appear in the
    evidence (so '6-month' grounds only if '6' is present near a duration
    word, approximated by a bare-digit presence test). For superlative
    words, a plain substring test.
    """
    digits = re.findall(r"\d[\d,]*(?:\.\d+)?", token)
    if digits:
        return all(d.replace(",", "") in evidence.replace(",", "")
                   for d in digits)
    return token in evidence


__all__ = ["FabricationCheck"]
