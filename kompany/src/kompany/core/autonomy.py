"""Autonomy gate — controls what needs approval."""

from __future__ import annotations


class AutonomyGate:
    """Checks whether a directive needs Master approval."""

    def __init__(self):
        self.thresholds = {
            "auto": 5.0,
            "ceo": 50.0,
        }

    def check(self, approval_tier: str, estimated_cost: float | None) -> bool:
        """Return True if the action can proceed without Master approval."""
        if approval_tier == "master":
            return False
        if approval_tier == "auto":
            return True
        if approval_tier == "ceo":
            cost = estimated_cost or 0
            return cost <= self.thresholds["ceo"]
        return False

    def check_tool(
        self,
        side_effect: str,
        autonomy_tier: str,
        estimated_cost_usd: float = 0.0,
        configured_auto: bool = False,
    ) -> bool:
        """Return True if a Tool call may execute inline without approval.

        Universal action pipeline (#4): only read-only, zero-cost,
        AUTO-tier tools run inline. PAID hard gate: a SPEND tool or any
        real cost NEVER auto-executes — ``configured_auto`` (e.g. a
        tool_authorization policy set to auto) cannot override it.
        """
        if side_effect == "spend" or (estimated_cost_usd or 0.0) > 0:
            return False  # no auto-pay, ever
        if side_effect != "read":
            # v1: side-effecting tools always go through the approval
            # queue. Founder-tunable auto-approve rules come later (#4).
            return False
        return autonomy_tier == "auto"
