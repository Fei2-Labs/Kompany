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
