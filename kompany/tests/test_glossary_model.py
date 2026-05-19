"""Pydantic-level tests for ``GlossaryEntry`` + ``CompanyGlossary``.

Covers the schema invariants the glossary-and-drift-detection task
(05-19) needs to hold: ``extra="forbid"``, self-reference rejection,
synonym normalisation, duplicate-term protection, and the prompt-summary
renderer.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from kompany.state.glossary import CompanyGlossary, GlossaryEntry, MAX_PROMPT_ENTRIES


def _entry(term: str = "customer", **overrides):
    base = {
        "term": term,
        "definition": "a paying account",
        "forbidden_synonyms": [],
        "added_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        "added_by": "founder",
    }
    base.update(overrides)
    return GlossaryEntry(**base)


def test_minimal_entry_constructs() -> None:
    entry = _entry()
    assert entry.term == "customer"
    assert entry.added_by == "founder"
    assert entry.forbidden_synonyms == []


def test_extra_forbid_rejects_typos() -> None:
    with pytest.raises(ValidationError):
        GlossaryEntry(
            term="customer",
            definition="a paying account",
            forbidd_synonyms=["user"],  # typo
            added_at=datetime.now(timezone.utc),
        )


def test_blank_term_rejected() -> None:
    with pytest.raises(ValidationError):
        _entry(term="   ")


def test_blank_definition_rejected() -> None:
    with pytest.raises(ValidationError):
        _entry(definition="")


def test_term_is_stripped_to_canonical_form() -> None:
    entry = _entry(term="  customer ")
    assert entry.term == "customer"


def test_self_reference_in_forbidden_synonyms_rejected() -> None:
    with pytest.raises(ValidationError):
        _entry(forbidden_synonyms=["Customer"])  # case-insensitive self-ref


def test_synonyms_dedupe_case_insensitively() -> None:
    entry = _entry(forbidden_synonyms=["user", "USER", "  user  ", "lead"])
    assert entry.forbidden_synonyms == ["user", "lead"]


def test_added_by_must_be_known_source() -> None:
    with pytest.raises(ValidationError):
        _entry(added_by="founder_friend")


def test_added_by_accepts_each_canonical_source() -> None:
    for src in ("founder", "cos_proposal", "template"):
        assert _entry(added_by=src).added_by == src


def test_company_glossary_add_then_find_case_insensitive() -> None:
    glossary = CompanyGlossary()
    glossary.add(_entry(term="customer"))
    assert glossary.find("Customer") is not None
    assert glossary.find("CUSTOMER") is not None
    assert glossary.find("user") is None


def test_company_glossary_add_duplicate_raises() -> None:
    glossary = CompanyGlossary()
    glossary.add(_entry(term="customer"))
    with pytest.raises(ValueError):
        glossary.add(_entry(term="customer"))
    # Case-insensitive duplicate also rejected.
    with pytest.raises(ValueError):
        glossary.add(_entry(term="Customer"))


def test_company_glossary_update_replaces_in_place() -> None:
    glossary = CompanyGlossary()
    glossary.add(_entry(term="customer"))
    updated = glossary.update(
        "customer",
        definition="updated definition",
        forbidden_synonyms=["user", "lead"],
    )
    assert updated.definition == "updated definition"
    assert updated.forbidden_synonyms == ["user", "lead"]
    # Position preserved (single-entry sanity).
    assert glossary.entries[0].term == "customer"


def test_company_glossary_update_rejects_self_reference_via_validator() -> None:
    glossary = CompanyGlossary()
    glossary.add(_entry(term="customer"))
    with pytest.raises(ValidationError):
        glossary.update("customer", forbidden_synonyms=["Customer"])


def test_company_glossary_remove_returns_true_when_present() -> None:
    glossary = CompanyGlossary()
    glossary.add(_entry(term="customer"))
    assert glossary.remove("CUSTOMER") is True
    assert len(glossary) == 0


def test_company_glossary_remove_returns_false_when_missing() -> None:
    glossary = CompanyGlossary()
    assert glossary.remove("anything") is False


def test_compose_summary_empty_returns_empty_string() -> None:
    assert CompanyGlossary().compose_summary() == ""


def test_compose_summary_lists_term_and_forbidden_synonyms() -> None:
    glossary = CompanyGlossary()
    glossary.add(_entry(term="customer", forbidden_synonyms=["user", "lead"]))
    summary = glossary.compose_summary()
    assert "customer" in summary
    assert "user" in summary
    assert "lead" in summary
    assert summary.startswith("COMPANY GLOSSARY")


def test_compose_summary_caps_at_max_entries() -> None:
    glossary = CompanyGlossary()
    # Add MAX + 5 entries so the summary needs to elide some.
    base_dt = datetime(2026, 5, 19, tzinfo=timezone.utc)
    for i in range(MAX_PROMPT_ENTRIES + 5):
        glossary.add(
            GlossaryEntry(
                term=f"term{i:03d}",
                definition=f"def {i}",
                forbidden_synonyms=[],
                added_at=base_dt,
            )
        )
    summary = glossary.compose_summary()
    assert "more" in summary  # ellipsis line
    # Body line count is capped at MAX + header + ellipsis.
    line_count = summary.count("\n") + 1
    assert line_count <= MAX_PROMPT_ENTRIES + 2
