"""Unit tests for ``kompany.agents.cos_glossary_scan.scan_drift``.

Glossary-and-drift-detection task 05-19. Covers the pure scan function
in isolation (no engine, no DB, no LLM): given an in-memory
:class:`CompanyGlossary` and three plain-dict text streams, the scanner
must return a deterministic list of :class:`DriftHit` rows that pinpoint
forbidden synonyms with the right agent attribution, count, and excerpt.
"""

from __future__ import annotations

from datetime import datetime, timezone

from kompany.agents.cos_glossary_scan import (
    DriftHit,
    build_suggested_corrections,
    scan_drift,
)
from kompany.state.glossary import CompanyGlossary, GlossaryEntry


def _entry(term: str, forbidden: list[str], definition: str = "") -> GlossaryEntry:
    return GlossaryEntry(
        term=term,
        definition=definition or f"canonical: {term}",
        forbidden_synonyms=forbidden,
        added_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        added_by="founder",
    )


def _glossary(*entries: GlossaryEntry) -> CompanyGlossary:
    g = CompanyGlossary()
    for e in entries:
        g.add(e)
    return g


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_glossary_returns_no_hits() -> None:
    g = CompanyGlossary()
    hits = scan_drift(
        glossary=g,
        reflections=[{"agent_role": "cmo", "content": "the user clicked"}],
    )
    assert hits == []


def test_glossary_with_no_forbidden_synonyms_returns_no_hits() -> None:
    g = _glossary(_entry("customer", []))
    hits = scan_drift(
        glossary=g,
        reflections=[{"agent_role": "cmo", "content": "the user clicked"}],
    )
    assert hits == []


def test_no_text_streams_returns_no_hits() -> None:
    g = _glossary(_entry("customer", ["user"]))
    hits = scan_drift(glossary=g)
    assert hits == []


# ---------------------------------------------------------------------------
# Reflection scans
# ---------------------------------------------------------------------------


def test_single_reflection_hit() -> None:
    g = _glossary(_entry("customer", ["user", "lead"]))
    hits = scan_drift(
        glossary=g,
        reflections=[
            {"agent_role": "cmo", "content": "Our user base grew this week."},
        ],
    )
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, DriftHit)
    assert hit.term == "customer"
    assert hit.drifted_synonym == "user"
    assert hit.agent_role == "cmo"
    assert hit.count == 1
    assert "user" in hit.sample_excerpt.lower()
    assert hit.source == "reflection"


def test_word_boundary_avoids_substring_false_positives() -> None:
    """``"super"`` must not trigger on ``"superuser"`` — \\b is required."""
    g = _glossary(_entry("customer", ["super"]))
    hits = scan_drift(
        glossary=g,
        reflections=[{"agent_role": "cmo", "content": "We use superuser auth."}],
    )
    assert hits == []


def test_case_insensitive_match() -> None:
    g = _glossary(_entry("customer", ["User"]))
    hits = scan_drift(
        glossary=g,
        reflections=[{"agent_role": "cmo", "content": "Our USER base."}],
    )
    assert len(hits) == 1
    assert hits[0].count == 1


def test_multiple_hits_aggregate_into_one_row() -> None:
    g = _glossary(_entry("customer", ["user"]))
    hits = scan_drift(
        glossary=g,
        reflections=[
            {
                "agent_role": "cmo",
                "content": "user growth, user churn, user retention",
            }
        ],
    )
    assert len(hits) == 1
    assert hits[0].count == 3


def test_different_agents_get_separate_rows() -> None:
    g = _glossary(_entry("customer", ["user"]))
    hits = scan_drift(
        glossary=g,
        reflections=[
            {"agent_role": "cmo", "content": "user growth"},
            {"agent_role": "cfo", "content": "user churn"},
        ],
    )
    assert len(hits) == 2
    roles = {h.agent_role for h in hits}
    assert roles == {"cmo", "cfo"}


# ---------------------------------------------------------------------------
# Decision + audit-event scans
# ---------------------------------------------------------------------------


def test_decision_attributes_to_first_agent_involved() -> None:
    g = _glossary(_entry("customer", ["lead"]))
    hits = scan_drift(
        glossary=g,
        decisions=[
            {
                "summary": "We agreed to chase every lead this quarter.",
                "agents_involved": ["cro", "cmo"],
            }
        ],
    )
    assert len(hits) == 1
    assert hits[0].agent_role == "cro"
    assert hits[0].source == "decision"


def test_decision_with_no_agents_falls_back_to_unknown() -> None:
    g = _glossary(_entry("customer", ["lead"]))
    hits = scan_drift(
        glossary=g,
        decisions=[{"summary": "Lead nurture starts Monday.", "agents_involved": []}],
    )
    assert len(hits) == 1
    assert hits[0].agent_role == "unknown"


def test_audit_event_detail_is_scanned() -> None:
    g = _glossary(_entry("revenue", ["yearly"]))
    hits = scan_drift(
        glossary=g,
        audit_events=[
            {
                "type": "cfo.report",
                "detail": {
                    "agent_role": "cfo",
                    "message": "yearly forecast looks tight",
                },
            }
        ],
    )
    assert len(hits) == 1
    assert hits[0].agent_role == "cfo"
    assert hits[0].source == "audit_event"


# ---------------------------------------------------------------------------
# Determinism + suggestions
# ---------------------------------------------------------------------------


def test_output_is_sorted_deterministically() -> None:
    g = _glossary(
        _entry("customer", ["user"]),
        _entry("revenue", ["income"]),
    )
    refls = [
        {"agent_role": "cmo", "content": "user click"},
        {"agent_role": "cfo", "content": "income growth"},
        {"agent_role": "cfo", "content": "user churn"},
    ]
    a = scan_drift(glossary=g, reflections=refls)
    b = scan_drift(glossary=g, reflections=refls)
    assert [h.model_dump() for h in a] == [h.model_dump() for h in b]


def test_build_suggested_corrections_renders_per_hit() -> None:
    g = _glossary(_entry("customer", ["user"], definition="paying account"))
    hits = scan_drift(
        glossary=g,
        reflections=[{"agent_role": "cmo", "content": "user base"}],
    )
    suggestions = build_suggested_corrections(hits, g)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s["term"] == "customer"
    assert s["drifted_synonym"] == "user"
    assert s["agent_role"] == "cmo"
    assert s["definition"] == "paying account"
    assert "customer" in s["suggested_replacement"]
    assert "user" in s["suggested_replacement"]
