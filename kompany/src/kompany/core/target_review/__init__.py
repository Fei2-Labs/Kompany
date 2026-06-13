"""Target feasibility review package.

Exposes ``TargetReviewMixin`` so call sites stay unchanged:
    from kompany.core.target_review import TargetReviewMixin
"""
from __future__ import annotations

from kompany.core.target_review._helpers import TargetReviewHelpersMixin
from kompany.core.target_review._llm_review import TargetReviewLLMMixin
from kompany.core.target_review._orchestration import TargetReviewOrchestrationMixin


class TargetReviewMixin(
    TargetReviewHelpersMixin,
    TargetReviewLLMMixin,
    TargetReviewOrchestrationMixin,
):
    """Full target-review mixin, composed from three sub-modules."""


__all__ = ["TargetReviewMixin"]
