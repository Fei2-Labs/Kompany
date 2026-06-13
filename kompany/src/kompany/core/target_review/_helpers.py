"""Helper utilities for the target feasibility review.

Contains the module-level ``_join_claim_texts`` utility and the
``TargetReviewHelpersMixin`` that exposes the two short static/instance
methods:

- ``_zero_per_agent_cost`` (static)
- ``_heuristic_recommend`` (instance)
"""

from __future__ import annotations

from typing import Any

from kompany.state.targets import CompanyTargets


def _join_claim_texts(claims: list[Any]) -> str:
    """Flatten a ``list[Claim]`` to a single string for legacy string fields."""
    parts = [str(getattr(c, "text", "")).strip() for c in claims]
    return " ".join(p for p in parts if p)


class TargetReviewHelpersMixin:
    """Short utility methods for the target-review concern."""

    @staticmethod
    def _zero_per_agent_cost() -> dict[str, dict[str, float]]:
        zero = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        return {"cfo": dict(zero), "cos": dict(zero), "ceo": dict(zero)}

    def _heuristic_recommend(
        self,
        founder: CompanyTargets,
        *,
        cash: float | None,
    ) -> CompanyTargets:
        """Produce a numerically conservative counter-proposal.

        Heuristic: if revenue_target > 5x initial_budget, recommend 60%
        of revenue_target. Pass deadline + customer_target through
        unchanged.
        """
        rev = founder.revenue_target
        if (
            founder.initial_budget > 0
            and rev > founder.initial_budget * 5
        ):
            rev = round(founder.revenue_target * 0.6, 2)
        return CompanyTargets(
            initial_budget=founder.initial_budget,
            revenue_target=rev,
            customer_target=founder.customer_target,
            deadline=founder.deadline,
            source="team_proposal",
        )
