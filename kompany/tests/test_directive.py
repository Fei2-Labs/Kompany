"""Tests for directive models and classification types."""

from __future__ import annotations

from kompany.core.directive import (
    Directive,
    DirectiveResult,
    DirectiveStatus,
    DirectiveType,
)


def test_directive_gets_auto_id():
    d = Directive(raw_input="Buy a Mac Studio")
    assert len(d.id) == 8


def test_directive_default_status_is_pending():
    d = Directive(raw_input="test")
    assert d.status == DirectiveStatus.PENDING


def test_directive_types():
    assert DirectiveType.ACQUISITION.value == "acquisition"
    assert DirectiveType.STRATEGIC.value == "strategic"
    assert DirectiveType.OPERATIONAL.value == "operational"
    assert DirectiveType.INFORMATIONAL.value == "informational"


def test_directive_result_structure():
    d = Directive(raw_input="test")
    r = DirectiveResult(
        directive=d,
        status="completed",
        message="Done",
        total_ai_cost=0.03,
        agents_used=["ceo"],
    )
    assert r.status == "completed"
    assert r.total_ai_cost == 0.03
    assert r.project_id is None
