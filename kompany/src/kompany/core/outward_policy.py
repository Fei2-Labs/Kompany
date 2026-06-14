"""Outward execution mode resolution (ADR-0008 #2).

One pure function, :func:`resolve_outward_mode`, decides whether an
outward action class may execute unattended (``'auto'``) or must be parked
for ``kompany approve`` (``'gated'``).

Resolution order:

1. **Policy lookup** — the operating project's per-action-class setting
   (:class:`kompany.state.outward_policies.OutwardActionPolicyStore`).
2. **Hard floors** — non-overridable downgrades to ``'gated'``, applied
   *after* the policy and enforced regardless of what it says:

   * any ``spend`` side-effect (mirrors ``core/autonomy.py``'s absolute
     no-auto-pay rule),
   * an estimated real cost over the founder's cap,
   * the ``founder_identity`` action class (acting AS the human).

The floors can only ever make the result *more* restrictive — they never
promote a ``'gated'`` policy to ``'auto'``. Pure: no I/O beyond the store
read, never raises on normal input.
"""

from __future__ import annotations

from typing import Any

from kompany.state.outward_policies import MODE_AUTO, MODE_GATED

# The action class that is always founder-identity work — acting AS the
# human, not the AI voice. Hard floor: never auto, no matter the policy.
FOUNDER_IDENTITY_CLASS = "founder_identity"

# Side-effect label that marks a money-moving action. Mirrors the
# ``side_effect == "spend"`` branch in core/autonomy.py.
SPEND_SIDE_EFFECT = "spend"


def resolve_outward_mode(
    policy_store: Any,
    action_class: str,
    *,
    side_effect: str = "",
    estimated_cost_usd: float = 0.0,
    founder_cost_cap: float = 0.0,
) -> str:
    """Resolve the effective execution mode for one outward action.

    Returns ``'auto'`` only when the policy says ``'auto'`` AND no hard
    floor fires; otherwise ``'gated'``. Hard floors (checked after the
    policy lookup, never overridden by it):

    * ``action_class == 'founder_identity'`` → always ``'gated'``.
    * ``side_effect == 'spend'`` → always ``'gated'`` (no auto-pay, ever).
    * ``estimated_cost_usd > founder_cost_cap`` → always ``'gated'``.

    ``founder_cost_cap`` is the founder's per-action auto cap in USD; a
    cost at or below it does not by itself trip the floor (a positive cost
    with a zero cap, i.e. ``0.0 < cost``, does).
    """
    policy_mode = MODE_GATED
    try:
        policy_mode = policy_store.get(action_class)
    except Exception:  # noqa: BLE001 — a store miss fails closed to gated
        policy_mode = MODE_GATED

    if policy_mode != MODE_AUTO:
        return MODE_GATED

    # Hard floors — applied after the policy, can only restrict.
    if action_class == FOUNDER_IDENTITY_CLASS:
        return MODE_GATED
    if (side_effect or "").strip().lower() == SPEND_SIDE_EFFECT:
        return MODE_GATED
    if (estimated_cost_usd or 0.0) > (founder_cost_cap or 0.0):
        return MODE_GATED

    return MODE_AUTO


__all__ = [
    "FOUNDER_IDENTITY_CLASS",
    "SPEND_SIDE_EFFECT",
    "resolve_outward_mode",
]
