"""CoS glossary drift scanner — runs at retrospective time.

This module is the **detection half** of the company glossary feature. The
CoS retrospective calls :func:`scan_drift` after agent reflections are
written but *before* the episode payload is materialised, so the resulting
:class:`DriftHit` rows can land both in a ``glossary_drift_alert`` health
event and in the episode payload's ``glossary_drift`` slot for later
distillation.

Detection algorithm (v1)
------------------------
Literal word-boundary, case-insensitive regex match. We compile one
``re.Pattern`` per forbidden synonym up-front (so a 50 KB episode with 20
glossary terms costs ~20 fast scans, not 50 KB × 20 substring searches),
then walk three text sources for each agent role:

1. **Reflections** — ``ReflectionEntry.content`` keyed by ``agent_role``.
2. **Decisions** — ``DecisionEntry.summary`` (these don't carry an agent
   role of their own; we attribute them to the *first* agent listed in
   ``agents_involved`` to give the founder a useful "blame target").
3. **Audit event details** — JSON-encoded ``AuditEvent.detail`` so any
   stringified message inside ("CFO said yearly revenue ...") still
   triggers a hit.

A drift counts once per ``(canonical_term, drifted_synonym, agent_role)``
tuple; multiple hits on the same tuple aggregate into a single
:class:`DriftHit` whose ``count`` is the total occurrences and whose
``sample_excerpt`` snapshots the first match.

The scanner is intentionally synchronous and side-effect-free. The
engine wraps it with the health-event write and the approval-request
creation so test fakes can drive the pure scan directly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from kompany.state.glossary import CompanyGlossary, GlossaryEntry


# How much surrounding text to include when capturing a sample excerpt.
# Wide enough to show context, narrow enough to keep the approval payload
# under the existing 50 KB episode-payload soft cap.
_EXCERPT_RADIUS = 60


class DriftHit(BaseModel):
    """One detected glossary violation inside an episode."""

    model_config = ConfigDict(extra="forbid")

    term: str = Field(
        description="The canonical term the founder defined.",
    )
    drifted_synonym: str = Field(
        description="The forbidden synonym the agent used instead.",
    )
    agent_role: str = Field(
        description="The agent role responsible for the drift "
        "(``cfo``, ``cmo``, ``cos``, ``unknown``, ...).",
    )
    count: int = Field(
        ge=1,
        description="Total occurrences of ``drifted_synonym`` attributed "
        "to this agent in the episode.",
    )
    sample_excerpt: str = Field(
        description="Short context window around the first match for the "
        "founder to read in the approval card.",
    )
    source: str = Field(
        default="reflection",
        description="Which text stream the first hit came from "
        "(``reflection`` / ``decision`` / ``audit_event``).",
    )


@dataclass(frozen=True)
class _CompiledTerm:
    """Internal: one canonical term + its precompiled synonym matchers."""

    term: str
    patterns: tuple[tuple[str, re.Pattern[str]], ...]


def _compile_glossary(
    glossary: CompanyGlossary,
) -> list[_CompiledTerm]:
    """Pre-build the regex objects so scan loops stay tight.

    Terms with no forbidden synonyms are skipped — they contribute
    nothing to drift detection (the canonical word itself is never
    forbidden).
    """
    compiled: list[_CompiledTerm] = []
    for entry in glossary.entries:
        if not entry.forbidden_synonyms:
            continue
        patterns: list[tuple[str, re.Pattern[str]]] = []
        for syn in entry.forbidden_synonyms:
            stripped = syn.strip()
            if not stripped:
                continue
            try:
                pat = re.compile(
                    r"\b" + re.escape(stripped) + r"\b",
                    re.IGNORECASE,
                )
            except re.error:
                # Defensive — re.escape should make every input regex-safe.
                continue
            patterns.append((stripped, pat))
        if patterns:
            compiled.append(_CompiledTerm(term=entry.term, patterns=tuple(patterns)))
    return compiled


def _make_excerpt(text: str, match: re.Match[str]) -> str:
    """Build a short ``...context HIT context...`` excerpt for the UI."""
    start = max(0, match.start() - _EXCERPT_RADIUS)
    end = min(len(text), match.end() + _EXCERPT_RADIUS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    # Collapse internal whitespace so the excerpt renders on one line.
    snippet = re.sub(r"\s+", " ", text[start:end]).strip()
    return f"{prefix}{snippet}{suffix}"


def _scan_text(
    text: str,
    *,
    agent_role: str,
    source: str,
    compiled: list[_CompiledTerm],
    aggregator: dict[tuple[str, str, str], DriftHit],
) -> None:
    """Walk every (term, synonym) pair against ``text`` and update hits.

    Aggregator key: ``(term, drifted_synonym, agent_role)``. The first
    hit captures the excerpt; later hits only bump the count so a chatty
    agent doesn't drown the inbox in duplicate cards.
    """
    if not text:
        return
    for cterm in compiled:
        for synonym, pattern in cterm.patterns:
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            key = (cterm.term, synonym, agent_role)
            existing = aggregator.get(key)
            if existing is None:
                aggregator[key] = DriftHit(
                    term=cterm.term,
                    drifted_synonym=synonym,
                    agent_role=agent_role,
                    count=len(matches),
                    sample_excerpt=_make_excerpt(text, matches[0]),
                    source=source,
                )
            else:
                # Preserve the original excerpt + source from the first
                # detection so the founder sees a stable anchor; only
                # the count grows.
                aggregator[key] = existing.model_copy(
                    update={"count": existing.count + len(matches)}
                )


def _stringify_detail(detail: Any) -> str:
    """Coerce ``AuditEvent.detail`` (dict) into one scannable string."""
    if not detail:
        return ""
    try:
        return json.dumps(detail, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(detail)


def scan_drift(
    *,
    glossary: CompanyGlossary,
    reflections: Iterable[Any] | None = None,
    decisions: Iterable[Any] | None = None,
    audit_events: Iterable[Any] | None = None,
) -> list[DriftHit]:
    """Detect glossary drift across the three episode text streams.

    Accepts duck-typed objects (Pydantic models from
    :mod:`kompany.state.episode_payload` *or* plain dicts) so callers
    can hand the function a partial episode payload during the
    pre-materialisation hook without rebuilding a full
    :class:`EpisodePayloadV1`.

    Edge cases:

    * Empty glossary → returns ``[]`` and exits before any scanning.
    * No reflections + no decisions + no audit events → returns ``[]``.
    * Glossary terms with empty ``forbidden_synonyms`` lists are silently
      skipped (they can't drift).

    Returns the deduplicated hits sorted by ``(agent_role, term)`` so the
    approval payload renders deterministically across test runs.
    """
    compiled = _compile_glossary(glossary)
    if not compiled:
        return []

    aggregator: dict[tuple[str, str, str], DriftHit] = {}

    # ---- Reflections (the highest-signal stream) -----------------------
    if reflections:
        for refl in reflections:
            role = _get_attr(refl, "agent_role", default="unknown")
            content = _get_attr(refl, "content", default="")
            _scan_text(
                str(content),
                agent_role=str(role),
                source="reflection",
                compiled=compiled,
                aggregator=aggregator,
            )

    # ---- Decisions (attribute to the first listed participant) ---------
    if decisions:
        for dec in decisions:
            summary = _get_attr(dec, "summary", default="")
            agents_involved = _get_attr(dec, "agents_involved", default=[]) or []
            if isinstance(agents_involved, (list, tuple)) and agents_involved:
                role = str(agents_involved[0])
            else:
                role = "unknown"
            _scan_text(
                str(summary),
                agent_role=role,
                source="decision",
                compiled=compiled,
                aggregator=aggregator,
            )

    # ---- Audit event details -------------------------------------------
    if audit_events:
        for ev in audit_events:
            detail = _get_attr(ev, "detail", default={})
            stringified = _stringify_detail(detail)
            # AuditEvent doesn't carry agent_role directly; we look for it
            # inside the detail dict, falling back to "unknown" so the
            # founder still sees the row in the inbox.
            role = "unknown"
            if isinstance(detail, dict):
                candidate = detail.get("agent_role") or detail.get("agent")
                if isinstance(candidate, str) and candidate:
                    role = candidate
            _scan_text(
                stringified,
                agent_role=role,
                source="audit_event",
                compiled=compiled,
                aggregator=aggregator,
            )

    hits = list(aggregator.values())
    hits.sort(key=lambda h: (h.agent_role, h.term, h.drifted_synonym))
    return hits


def _get_attr(obj: Any, name: str, *, default: Any) -> Any:
    """Read ``name`` off a Pydantic model OR a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def build_suggested_corrections(
    drifts: list[DriftHit],
    glossary: CompanyGlossary,
) -> list[dict[str, Any]]:
    """For each drift, render the founder-facing correction proposal.

    The output is wired into the ``glossary_review`` approval payload so
    the UI can show ``"Use customer instead of user (CMO 3x)"`` without
    a second lookup against the glossary.
    """
    suggestions: list[dict[str, Any]] = []
    for hit in drifts:
        entry = glossary.find(hit.term)
        definition = entry.definition if entry is not None else ""
        suggestions.append({
            "term": hit.term,
            "drifted_synonym": hit.drifted_synonym,
            "agent_role": hit.agent_role,
            "count": hit.count,
            "definition": definition,
            "suggested_replacement": (
                f"Use {hit.term!r} instead of {hit.drifted_synonym!r} "
                f"({hit.agent_role} used the synonym {hit.count}x)."
            ),
        })
    return suggestions


__all__ = [
    "DriftHit",
    "build_suggested_corrections",
    "scan_drift",
]
