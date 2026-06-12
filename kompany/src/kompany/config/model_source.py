"""ModelSource — the founder-facing model/billing configuration.

PRD ``06-11-harness-execution-leg`` D1/D2: the founder picks a *model
source* (Hermes/OpenClaw-style UX) — custom API key, Claude subscription,
OpenAI subscription. Everything else is derived:

* The execution **vehicle** (which CLI loop runs harness sessions) is an
  internal consequence of the source kind — subscription compute cannot
  leave its own CLI. It is exposed here as a computed property and must
  NEVER appear as a founder-facing setting ("runner" is not a product
  concept).
* The **billing_mode** decides how LLM spend hits the company ledger:
  ``api`` books per-token cost as a real expense (existing behavior);
  ``subscription`` books the monthly fee as a recurring expense and
  records per-call spend as *shadow value* only (never touches balance).

``price_overrides`` lets the founder fill/override per-token pricing for
private deployments; overrides are consulted before the built-in
``PRICING`` table (see :func:`kompany.llm.models.resolve_cost`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

SourceKind = Literal["custom_api", "claude_subscription", "openai_subscription"]
BillingMode = Literal["api", "subscription"]

# Derived billing default per source kind (PRD D2). Overridable — e.g. a
# founder may run a "claude_subscription" source pay-as-you-go during a
# trial month by forcing billing_mode="api".
_DEFAULT_BILLING_MODE: dict[str, BillingMode] = {
    "custom_api": "api",
    "claude_subscription": "subscription",
    "openai_subscription": "subscription",
}

# PRD D1 — engine-internal vehicle per source kind. Subscription compute
# is physically locked to its own CLI; API keys ride the provider-agnostic
# opencode loop. Later sources (kiro, cursor-agent, ...) add rows here.
_VEHICLE_BY_KIND: dict[str, str] = {
    "claude_subscription": "claude_code",
    "openai_subscription": "codex",
    "custom_api": "opencode",
}

# Which *single-call* model ids route through the subscription's own CLI
# provider. Mirrors ``_MODEL_PREFIX_MAP`` in ``llm/providers.py``:
# ``claude-code:*`` ids shell out to the local ``claude`` CLI (subscription
# auth). OpenAI subscription has no single-call CLI provider yet (codex is
# harness-only); harness results reach the ledger via
# ``CostTracker.record_external`` which branches on billing_mode directly.
_SUBSCRIPTION_CLI_PREFIXES: dict[str, tuple[str, ...]] = {
    "claude_subscription": ("claude-code",),
    "openai_subscription": (),
    "custom_api": (),
}


class ModelSource(BaseModel):
    """A single configured model source (MVP: one active source).

    ``billing_mode`` defaults from ``kind`` (custom_api → api,
    *_subscription → subscription) but may be set explicitly.
    ``monthly_fee_usd`` is mandatory when billing_mode is subscription —
    the fee is the real recurring ledger expense for the source.
    """

    kind: SourceKind
    billing_mode: BillingMode | None = None
    monthly_fee_usd: float | None = None
    # Per-model (input_usd, output_usd) per **million** tokens. Consulted
    # before the built-in PRICING table. TODO(models.dev): auto-pull
    # official per-token pricing as a later enhancement — founder
    # fill/override here is the guaranteed MVP path (PRD D2).
    price_overrides: dict[str, tuple[float, float]] | None = None

    @model_validator(mode="after")
    def _derive_and_validate(self) -> "ModelSource":
        if self.billing_mode is None:
            self.billing_mode = _DEFAULT_BILLING_MODE[self.kind]
        if self.billing_mode == "subscription":
            if self.monthly_fee_usd is None:
                raise ValueError(
                    "monthly_fee_usd is required when billing_mode is "
                    "'subscription' — the monthly fee is the source's real "
                    "recurring ledger expense"
                )
            if self.monthly_fee_usd < 0:
                raise ValueError("monthly_fee_usd must be >= 0")
        return self

    @property
    def vehicle(self) -> str:
        """Engine-internal harness vehicle for this source (PRD D1).

        Computed, never configured: ``claude_subscription`` → claude CLI
        loop, ``openai_subscription`` → codex CLI loop, ``custom_api`` →
        opencode loop. Never surface this in founder-facing UI/docs.
        """
        return _VEHICLE_BY_KIND[self.kind]

    def is_subscription_call(self, model: str) -> bool:
        """True when a single-shot call to ``model`` rides this source's
        subscription (→ real marginal cost 0, shadow value only).

        Only meaningful for subscription billing; in api mode every call
        is a real per-token expense regardless of model.
        """
        if self.billing_mode != "subscription":
            return False
        prefixes = _SUBSCRIPTION_CLI_PREFIXES.get(self.kind, ())
        model_lower = model.lower()
        return any(model_lower.startswith(prefix) for prefix in prefixes)


__all__ = ["BillingMode", "ModelSource", "SourceKind"]
