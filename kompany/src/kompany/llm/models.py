"""Model tier configuration and pricing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_mtok: float  # USD per million input tokens
    output_per_mtok: float  # USD per million output tokens


# Pricing as of early 2025 — update as needed
PRICING: dict[str, ModelPricing] = {
    "claude-opus-4-20250514": ModelPricing(15.0, 75.0),
    "claude-sonnet-4-20250514": ModelPricing(3.0, 15.0),
    "claude-haiku-4-20250414": ModelPricing(0.80, 4.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a single LLM call."""
    pricing = PRICING.get(model)
    if not pricing:
        # Fallback: assume Sonnet pricing
        pricing = ModelPricing(3.0, 15.0)
    return (
        input_tokens * pricing.input_per_mtok / 1_000_000
        + output_tokens * pricing.output_per_mtok / 1_000_000
    )
