"""Tests for the outward pre-flight pipeline (ADR-0008 #3).

The three underlying gates are mocked at their call sites in
``core.outward_preflight`` so we assert ONLY the orchestration: order,
short-circuit on the first hold, and ok=True when all pass. No LLM /
agent / network.
"""

from __future__ import annotations

import pytest

from kompany.core import outward_preflight
from kompany.core.deai_gate import GateResult
from kompany.core.deliverables import DeliverableClass
from kompany.core.outward_preflight import PreflightResult, run_preflight


class FakeEngine:
    def get_company_state(self):
        return {"name": "Acme"}


def _action(text="some outward copy"):
    return {
        "text": text,
        "deliverable_class": DeliverableClass.PUBLISHED_CONTENT,
    }


def _patch_gates(monkeypatch, csuite, deai, fab, calls):
    """Patch each per-gate runner to a scripted GateResult, recording order."""

    def make(name, result):
        def runner(*args, **kwargs):
            calls.append(name)
            return result
        return runner

    monkeypatch.setattr(
        outward_preflight, "_run_csuite_gate",
        lambda engine, action, cls, text: (
            calls.append("csuite") or csuite
        ),
    )
    monkeypatch.setattr(
        outward_preflight, "_run_deai_gate",
        lambda engine, text: (calls.append("deai") or deai),
    )
    monkeypatch.setattr(
        outward_preflight, "_run_fabrication_gate",
        lambda engine, text: (calls.append("fabrication") or fab),
    )


def _pass(gate):
    return GateResult(verdict="pass", gate=gate)


def _hold(gate):
    return GateResult(verdict="hold", findings=[f"{gate} held"], gate=gate)


# ---------------------------------------------------------------------------
# All pass
# ---------------------------------------------------------------------------


def test_all_gates_pass_returns_ok(monkeypatch):
    calls: list[str] = []
    _patch_gates(
        monkeypatch,
        _pass("csuite_review"), _pass("deai"), _pass("fabrication"), calls,
    )
    result = run_preflight(FakeEngine(), _action())
    assert isinstance(result, PreflightResult)
    assert result.ok is True
    assert result.holds == []
    assert calls == ["csuite", "deai", "fabrication"]
    assert result.passed == ["csuite_review", "deai", "fabrication"]


# ---------------------------------------------------------------------------
# Short-circuit on the first hold
# ---------------------------------------------------------------------------


def test_short_circuits_on_csuite_hold(monkeypatch):
    calls: list[str] = []
    _patch_gates(
        monkeypatch,
        _hold("csuite_review"), _pass("deai"), _pass("fabrication"), calls,
    )
    result = run_preflight(FakeEngine(), _action())
    assert result.ok is False
    assert [h.gate for h in result.holds] == ["csuite_review"]
    # de-AI and fabrication never ran.
    assert calls == ["csuite"]
    assert result.passed == []


def test_short_circuits_on_deai_hold(monkeypatch):
    calls: list[str] = []
    _patch_gates(
        monkeypatch,
        _pass("csuite_review"), _hold("deai"), _pass("fabrication"), calls,
    )
    result = run_preflight(FakeEngine(), _action())
    assert result.ok is False
    assert [h.gate for h in result.holds] == ["deai"]
    # fabrication never ran; csuite passed first.
    assert calls == ["csuite", "deai"]
    assert result.passed == ["csuite_review"]


def test_holds_on_fabrication(monkeypatch):
    calls: list[str] = []
    _patch_gates(
        monkeypatch,
        _pass("csuite_review"), _pass("deai"), _hold("fabrication"), calls,
    )
    result = run_preflight(FakeEngine(), _action())
    assert result.ok is False
    assert [h.gate for h in result.holds] == ["fabrication"]
    assert calls == ["csuite", "deai", "fabrication"]
    assert result.passed == ["csuite_review", "deai"]


# ---------------------------------------------------------------------------
# Advisory rewrite does not block
# ---------------------------------------------------------------------------


def test_deai_rewrite_does_not_block(monkeypatch):
    calls: list[str] = []
    _patch_gates(
        monkeypatch,
        _pass("csuite_review"),
        GateResult(verdict="rewrite", findings=["smells"], gate="deai"),
        _pass("fabrication"),
        calls,
    )
    result = run_preflight(FakeEngine(), _action())
    assert result.ok is True
    assert calls == ["csuite", "deai", "fabrication"]
