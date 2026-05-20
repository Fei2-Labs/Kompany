"""Tests for the PREVIEW layer of the cost visibility discipline."""

from __future__ import annotations

import pytest

from kompany.llm.cost_preview import (
    DEFAULT_THRESHOLD_USD,
    CostPreview,
    preview_cost,
)
from kompany.llm.models import estimate_cost


def test_preview_returns_cost_preview_instance():
    p = preview_cost(
        prompt="Hello, world.",
        model="claude-sonnet-4-20250514",
        max_output_tokens=200,
        action_type="ping",
    )
    assert isinstance(p, CostPreview)
    assert p.action_type == "ping"
    assert p.model == "claude-sonnet-4-20250514"


def test_preview_empty_prompt_zero_input_tokens():
    p = preview_cost(
        prompt="",
        model="claude-sonnet-4-20250514",
        max_output_tokens=100,
    )
    assert p.est_input_tokens == 0
    # est_output_tokens applies the 0.8 utilisation factor.
    assert p.est_output_tokens == 80


def test_preview_input_token_heuristic_four_chars_per_token():
    # 40 chars / 4 chars-per-token = 10 tokens
    p = preview_cost(
        prompt="a" * 40,
        model="claude-sonnet-4-20250514",
        max_output_tokens=100,
    )
    assert p.est_input_tokens == 10


def test_preview_uses_live_pricing_table():
    in_toks = 10
    out_toks = 80
    expected = estimate_cost("claude-sonnet-4-20250514", in_toks, out_toks)
    p = preview_cost(
        prompt="a" * (in_toks * 4),
        model="claude-sonnet-4-20250514",
        max_output_tokens=100,  # 100 * 0.8 = 80
    )
    assert p.est_cost_usd == pytest.approx(expected)


def test_preview_threshold_default_and_override():
    # A tiny call should fall below the 0.01 default.
    p_small = preview_cost(
        prompt="hi",
        model="claude-haiku-4-20250414",
        max_output_tokens=50,
    )
    assert p_small.threshold_usd == DEFAULT_THRESHOLD_USD
    assert p_small.below_threshold is True

    # Override threshold to 0 — every non-zero cost should be above.
    p_zero = preview_cost(
        prompt="hi",
        model="claude-haiku-4-20250414",
        max_output_tokens=50,
        threshold_usd=0.0,
    )
    assert p_zero.threshold_usd == 0.0
    assert p_zero.below_threshold is False


def test_preview_below_threshold_for_expensive_call():
    """Bigger prompt + apex model should cross $0.01."""
    p = preview_cost(
        prompt="word " * 4000,  # ~5000 tokens of input
        model="claude-opus-4-20250514",
        max_output_tokens=2000,
    )
    assert p.below_threshold is False
    assert p.est_cost_usd > DEFAULT_THRESHOLD_USD


def test_preview_balance_after_subtracts_estimate():
    p = preview_cost(
        prompt="a" * 40,
        model="claude-sonnet-4-20250514",
        max_output_tokens=200,
        current_balance=100.0,
    )
    assert p.ledger_balance_now == 100.0
    assert p.ledger_balance_after_estimate == pytest.approx(100.0 - p.est_cost_usd)


def test_preview_zero_max_output_tokens():
    p = preview_cost(
        prompt="hello",
        model="claude-sonnet-4-20250514",
        max_output_tokens=0,
    )
    assert p.est_output_tokens == 0
