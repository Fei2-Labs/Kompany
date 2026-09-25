"""Approval-routing judgment consultation (ADR-0011).

The judgment seam (``core/judgment.py``, ADR-0010) picked its first
consumer: the founder-tunable auto-approve check in
``core/engine_parts/surfaces.py._auto_approve_eligible``. This module
holds the actual consultation so that file — already over the ADR-0003
line budget — gains one import and one call, not a new block of logic.

The wiring is TIGHTEN-ONLY, matching every other layer that already
touches this decision (``AutonomyGate``'s no-auto-pay floor,
``outward_policy``'s hard floors): a judgment call can turn an
already-eligible auto-approve into a hold, never the reverse. Nothing
here can approve an action the deterministic ``tool_authorization``
policy did not already allow.

With no provider installed — every install today — ``judge()`` returns
an abstention and :func:`judgment_confirms_auto_approve` is a no-op:
the policy's own answer stands. This file has zero live effect until
(a) a provider is installed and (b), if it is external, the founder
has opted in via ``external_judgment_enabled``. See ADR-0011 for the
threshold choice and why it is conservative.
"""

from __future__ import annotations

from typing import Any

from kompany.core.judgment import BooleanQuestion, judge

# A verdict must clear BOTH bars to override an already-eligible
# auto-approve. Conservative on purpose: interrupting the founder is a
# real cost, not a neutral default, so a weak or ambiguous signal must
# not fire it. See ADR-0011.
_HOLD_PROBABILITY_THRESHOLD = 0.65
_MIN_CONFIDENCE = 0.6


def judgment_confirms_auto_approve(
    engine: Any,
    *,
    tool_name: str,
    tool_description: str,
    side_effect: str,
    estimated_cost_usd: float,
    requested_by: str,
) -> bool:
    """True unless a usable, confident judgment says this needs review.

    Call only after the deterministic ``tool_authorization`` policy has
    already allowed unattended execution — this can subtract that
    eligibility, never grant it.
    """
    question = BooleanQuestion(
        instructions=(
            "A company's own auto-approve policy already allows this "
            "specific tool call to run unattended, with no human "
            "review. Given the tool, its description, who requested "
            "it and its side effect, should the founder still see it "
            "before it runs — is there something unusual enough here "
            "that the standing policy should not just apply blindly?"
        ),
        when_true="Yes — hold this one for the founder despite the policy.",
        when_false="No — this is routine; the standing policy should apply.",
    )
    state = {
        "tool_name": tool_name,
        "tool_description": tool_description,
        "side_effect": side_effect,
        "estimated_cost_usd": estimated_cost_usd,
        "requested_by": requested_by,
    }
    answers = judge({"hold_despite_policy": question}, state, engine=engine)
    verdict = answers["hold_despite_policy"]
    if not verdict.usable:
        return True  # no provider, or it abstained — policy stands
    should_hold = (
        isinstance(verdict.value, (int, float))
        and verdict.value >= _HOLD_PROBABILITY_THRESHOLD
        and verdict.confidence >= _MIN_CONFIDENCE
    )
    return not should_hold


__all__ = ["judgment_confirms_auto_approve"]
