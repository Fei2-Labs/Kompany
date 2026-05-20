"""Pre-call cost estimation — PREVIEW layer of the three-layer cost
visibility discipline (see ``engineering-cost-visibility-discipline``
memory + PRD ``05-19-cost-visibility-discipline``).

The PREVIEW layer answers a question UI components need to ask **before**
spending money: "if I run this LLM call, roughly how much will it cost?"

Critically this module does **not** call any LLM. Token counts are
estimated from string length (one token ≈ 4 chars for English / mixed
content, matching the long-standing OpenAI heuristic), and output tokens
are taken from the caller-supplied ``max_output_tokens`` knob multiplied
by an 80% utilisation factor (over-estimate so the UI never
under-promises burn).

A typical caller in the web UI does:

    preview = preview_cost(prompt, model, max_output_tokens=600,
                           current_balance=ledger.get_balance())
    if not preview.below_threshold:
        confirm = await confirm_costly(preview)
        if not confirm:
            return
    # ...proceed with the actual LLM call...

The threshold is a per-company knob (``cost_preview_threshold_usd`` in
``company_config``); pass it explicitly via ``threshold_usd`` if you want
a non-default value.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from kompany.llm.models import estimate_cost

# Default threshold below which a UI may skip the "are you sure?" modal.
# Mirrors ``company_config['cost_preview_threshold_usd']`` default.
DEFAULT_THRESHOLD_USD = 0.01

# Token-per-character heuristic. 4.0 chars/token is the conservative
# OpenAI rule-of-thumb for English / mixed-language prompts. We don't
# pull in ``tiktoken`` so the preview path stays dependency-light.
_CHARS_PER_TOKEN = 4.0

# Output utilisation factor. Callers pass ``max_output_tokens`` (the
# upper bound). We multiply by 0.8 so the preview leans toward
# over-estimating without claiming the model will always max out.
_OUTPUT_UTILISATION = 0.8


class CostPreview(BaseModel):
    """Pre-call cost preview payload."""

    action_type: str = Field(
        description="Stable label for the call site, e.g. 'target_feasibility'."
    )
    model: str
    est_input_tokens: int
    est_output_tokens: int
    est_cost_usd: float
    ledger_balance_now: float
    ledger_balance_after_estimate: float
    threshold_usd: float = DEFAULT_THRESHOLD_USD
    below_threshold: bool = Field(
        description=(
            "True when ``est_cost_usd <= threshold_usd``. UI components "
            "use this to skip the confirmation modal for cheap calls."
        )
    )


def _estimate_input_tokens(prompt: str) -> int:
    """Estimate input tokens from prompt length.

    Uses a 4-chars-per-token heuristic. Returns at least 1 for non-empty
    input so callers never see a zero-cost preview for a real prompt.
    """
    if not prompt:
        return 0
    return max(1, int(len(prompt) / _CHARS_PER_TOKEN))


def _estimate_output_tokens(max_output_tokens: int) -> int:
    """Estimate output tokens from the caller-supplied max.

    80% utilisation: not every call maxes out, but we over-estimate so
    the UI never tells the founder "this will cost $0.02" and then
    actually charges $0.03.
    """
    if max_output_tokens <= 0:
        return 0
    return max(1, int(max_output_tokens * _OUTPUT_UTILISATION))


def preview_cost(
    prompt: str,
    model: str,
    max_output_tokens: int,
    *,
    action_type: str = "other",
    current_balance: float = 0.0,
    threshold_usd: float = DEFAULT_THRESHOLD_USD,
) -> CostPreview:
    """Return a :class:`CostPreview` for a hypothetical LLM call.

    No LLM is invoked. Token counts are heuristic; cost is computed via
    :func:`kompany.llm.models.estimate_cost` against the live pricing
    table so prices stay in sync with the ledger writer.
    """
    in_tokens = _estimate_input_tokens(prompt)
    out_tokens = _estimate_output_tokens(max_output_tokens)
    est_cost = estimate_cost(model, in_tokens, out_tokens)
    # AI cost is recorded as a negative ledger amount, so the projected
    # balance is the current balance MINUS the estimated cost magnitude.
    after = current_balance - est_cost
    return CostPreview(
        action_type=action_type,
        model=model,
        est_input_tokens=in_tokens,
        est_output_tokens=out_tokens,
        est_cost_usd=est_cost,
        ledger_balance_now=current_balance,
        ledger_balance_after_estimate=after,
        threshold_usd=threshold_usd,
        below_threshold=est_cost <= threshold_usd,
    )


__all__ = [
    "CostPreview",
    "DEFAULT_THRESHOLD_USD",
    "preview_cost",
]
