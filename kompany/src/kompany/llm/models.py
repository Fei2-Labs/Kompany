"""Model tier configuration and pricing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_mtok: float  # USD per million input tokens
    output_per_mtok: float  # USD per million output tokens


# Pricing as of early 2025 — update as needed
PRICING: dict[str, ModelPricing] = {
    # Anthropic
    "claude-opus-4-20250514": ModelPricing(15.0, 75.0),
    "claude-sonnet-4-20250514": ModelPricing(3.0, 15.0),
    "claude-haiku-4-20250414": ModelPricing(0.80, 4.0),
    # claude-code CLI (subscription auth). The CLI reports its own
    # total_cost_usd, but ledger estimates go through this table like
    # every other provider — use the API-equivalent rate per tier alias
    # so opus calls aren't silently billed at the Sonnet-tier
    # "claude-" prefix fallback.
    "claude-code:opus": ModelPricing(15.0, 75.0),
    "claude-code:sonnet": ModelPricing(3.0, 15.0),
    "claude-code:haiku": ModelPricing(0.80, 4.0),
    # codex CLI (ChatGPT-subscription auth, issue #18). Same discipline:
    # shadow/estimate values use the API-equivalent OpenAI rates
    # (gpt-5: 1.25/10.0, gpt-5-mini: 0.25/2.0 USD per MTok).
    "codex:gpt-5": ModelPricing(1.25, 10.0),
    "codex:gpt-5-codex": ModelPricing(1.25, 10.0),
    "codex:gpt-5-mini": ModelPricing(0.25, 2.0),
    # opencode CLI (issue #18). Model suffix is opencode's native
    # ``provider/model`` form; common ids get exact API-equivalent rows.
    "opencode:openai/gpt-5": ModelPricing(1.25, 10.0),
    "opencode:anthropic/claude-sonnet-4-20250514": ModelPricing(3.0, 15.0),
    # OpenAI
    "gpt-4o": ModelPricing(2.50, 10.0),
    "gpt-4o-mini": ModelPricing(0.15, 0.60),
    "gpt-4.1": ModelPricing(2.0, 8.0),
    "gpt-4.1-mini": ModelPricing(0.40, 1.60),
    "gpt-4.1-nano": ModelPricing(0.10, 0.40),
    "o3": ModelPricing(2.0, 8.0),
    "o3-mini": ModelPricing(1.10, 4.40),
    "o4-mini": ModelPricing(1.10, 4.40),
    # Gemini
    "gemini-2.5-pro": ModelPricing(1.25, 10.0),
    "gemini-2.5-flash": ModelPricing(0.15, 0.60),
    "gemini-2.0-flash": ModelPricing(0.10, 0.40),
    "gemini-2.0-flash-lite": ModelPricing(0.075, 0.30),
    # GLM (Zhipu AI) — prices converted from CNY at ~7.2 CNY/USD
    "glm-4-plus": ModelPricing(7.0, 7.0),
    "glm-4-air": ModelPricing(0.14, 0.14),
    "glm-4-airx": ModelPricing(1.40, 1.40),
    "glm-4-flash": ModelPricing(0.0, 0.0),
    "glm-4-flashx": ModelPricing(0.07, 0.07),
    # Kimi (Moonshot)
    "moonshot-v1-8k": ModelPricing(1.67, 1.67),
    "moonshot-v1-32k": ModelPricing(3.33, 3.33),
    "moonshot-v1-128k": ModelPricing(8.33, 8.33),
    "kimi-latest": ModelPricing(1.67, 1.67),
}

# Prefix-based fallback: if exact model not in PRICING, try prefix match.
# Uses representative mid-tier pricing for each provider.
_FALLBACK_BY_PREFIX: list[tuple[str, ModelPricing]] = [
    # CLI providers (issue #18): codex serves OpenAI models → gpt-5-tier
    # API-equivalent; opencode routes to any provider → conservative
    # Sonnet-tier default (same basis as _DEFAULT_FALLBACK).
    ("codex:", ModelPricing(1.25, 10.0)),
    ("opencode:", ModelPricing(3.0, 15.0)),
    ("claude-", ModelPricing(3.0, 15.0)),       # Sonnet-tier
    ("gpt-", ModelPricing(2.50, 10.0)),          # GPT-4o tier
    ("o1", ModelPricing(2.0, 8.0)),
    ("o3", ModelPricing(2.0, 8.0)),
    ("o4", ModelPricing(1.10, 4.40)),
    ("gemini-", ModelPricing(0.15, 0.60)),       # Flash-tier
    ("glm-", ModelPricing(0.14, 0.14)),          # Air-tier
    ("moonshot-", ModelPricing(1.67, 1.67)),
    ("kimi-", ModelPricing(1.67, 1.67)),
]

# Default fallback for completely unknown models
_DEFAULT_FALLBACK = ModelPricing(3.0, 15.0)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a single LLM call."""
    # 1. Exact match
    pricing = PRICING.get(model)

    # 2. Prefix fallback
    if not pricing:
        model_lower = model.lower()
        for prefix, fallback_pricing in _FALLBACK_BY_PREFIX:
            if model_lower.startswith(prefix):
                pricing = fallback_pricing
                break

    # 3. Default fallback (Sonnet-tier)
    if not pricing:
        pricing = _DEFAULT_FALLBACK

    return (
        input_tokens * pricing.input_per_mtok / 1_000_000
        + output_tokens * pricing.output_per_mtok / 1_000_000
    )


def resolve_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    overrides: dict[str, tuple[float, float]] | None = None,
) -> float:
    """Estimate USD cost, consulting founder ``price_overrides`` first.

    ``overrides`` maps model id → (input_usd, output_usd) per **million**
    tokens (the ``ModelSource.price_overrides`` shape). An exact-match
    override wins over everything; otherwise this delegates to
    :func:`estimate_cost`, whose documented 3-step resolution order
    (exact PRICING → prefix fallback → default) is unchanged for all
    non-override callers.

    TODO(models.dev): auto-pull official per-token pricing into PRICING
    as later work (PRD 06-11-harness-execution-leg scopes auto-pull
    loosely) — founder fill/override via ``overrides`` is the guaranteed
    path for private deployments.
    """
    if overrides:
        override = overrides.get(model)
        if override is not None:
            input_per_mtok, output_per_mtok = override
            return (
                input_tokens * input_per_mtok / 1_000_000
                + output_tokens * output_per_mtok / 1_000_000
            )
    return estimate_cost(model, input_tokens, output_tokens)
