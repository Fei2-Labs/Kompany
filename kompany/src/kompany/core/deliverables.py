"""Deliverable classification — the C-suite review gate's front door (ADR-0007).

When a task resolves to delivered/completed, the engine asks one question:
*is this thing going to leave the building?* Internal notes, research memos,
and back-office work ship freely. Anything outward-facing — a customer email,
a published post, a product launch, an irreversible strategic call — must pass
an adversarial C-suite review before the actual outward action (send / post /
launch) is allowed. This module answers the classification half:

* :class:`DeliverableClass` — the five buckets.
* :func:`classify_deliverable` — keyword + action-type heuristics over the
  task title (and optional action_type / project_type). Pure, no I/O.
* :func:`needs_csuite_review` — True for everything except ``INTERNAL``.
* :data:`REVIEW_ROLES` — which executives review each outward class. Role codes
  are lowercase to match the agent registry / debate_prep vocabulary.

The classification is intentionally conservative: an ambiguous deliverable
that *could* be outward-facing is treated as outward (review gate fires)
rather than slipping out unreviewed. Code-only, deterministic, fully testable.
"""

from __future__ import annotations

from enum import Enum


class DeliverableClass(str, Enum):
    """How far a finished deliverable travels — drives the review gate."""

    INTERNAL = "internal"
    EXTERNAL_COMMUNICATION = "external_communication"
    PUBLISHED_CONTENT = "published_content"
    PRODUCT_LAUNCH = "product_launch"
    CRITICAL_DECISION = "critical_decision"


# Deliverable class → C-suite reviewer roles (lowercase registry codes).
# Each outward class convenes the executives who own the risk it carries.
# INTERNAL has no reviewers (it never reaches the gate).
REVIEW_ROLES: dict[DeliverableClass, list[str]] = {
    DeliverableClass.INTERNAL: [],
    DeliverableClass.EXTERNAL_COMMUNICATION: ["cmo", "cro"],
    DeliverableClass.PUBLISHED_CONTENT: ["cmo", "cv"],
    DeliverableClass.PRODUCT_LAUNCH: ["cpo", "cto", "cfo"],
    DeliverableClass.CRITICAL_DECISION: ["ceo", "cfo", "coo"],
}


# Action types whose outward intent is unambiguous, mapped straight to a
# class (checked before the title keyword scan). These mirror the existing
# outbound-gated surfaces: channel posts, emails, tool sends.
_ACTION_TYPE_CLASS: dict[str, DeliverableClass] = {
    "channel_post": DeliverableClass.PUBLISHED_CONTENT,
    "email": DeliverableClass.EXTERNAL_COMMUNICATION,
    "email_send": DeliverableClass.EXTERNAL_COMMUNICATION,
    "tool_action": DeliverableClass.EXTERNAL_COMMUNICATION,
    "send_email": DeliverableClass.EXTERNAL_COMMUNICATION,
}


# Keyword buckets scanned over the task title (lowercased). Order matters:
# the first class whose keywords match wins, and the list is ordered by
# blast radius (most consequential first) so a "launch pricing decision"
# resolves to the higher-stakes class.
_CLASS_KEYWORDS: list[tuple[DeliverableClass, tuple[str, ...]]] = [
    (
        DeliverableClass.CRITICAL_DECISION,
        (
            "pivot", "shut down", "shutdown", "wind down", "acquire",
            "acquisition", "merger", "fundrais", "raise round", "term sheet",
            "lay off", "layoff", "restructur", "sign contract",
            "legal agreement", "irreversible", "bet the company",
            "strategic decision", "go/no-go", "go no-go",
        ),
    ),
    (
        DeliverableClass.PRODUCT_LAUNCH,
        (
            "launch", "ship to production", "release to users", "go live",
            "public release", "rollout", "roll out", "general availability",
            "ga release", "deploy to prod", "production deploy",
        ),
    ),
    (
        DeliverableClass.PUBLISHED_CONTENT,
        (
            "publish", "blog post", "blog article", "post to", "tweet",
            "x post", "linkedin post", "press release", "newsletter",
            "social media", "landing page copy", "marketing copy",
            "public statement", "announcement post", "medium article",
        ),
    ),
    (
        DeliverableClass.EXTERNAL_COMMUNICATION,
        (
            "email", "outreach", "cold email", "reply to customer",
            "customer message", "send to client", "send to customer",
            "proposal to", "quote to", "respond to lead", "investor update",
            "reach out", "dm ", "message the", "contact the customer",
        ),
    ),
]


def classify_deliverable(
    task_title: str,
    action_type: str | None = None,
    project_type: str | None = None,
) -> DeliverableClass:
    """Classify a finished deliverable by outward reach.

    Priority: explicit ``action_type`` mapping → title keyword scan
    (most-consequential class first) → ``INTERNAL`` default. ``project_type``
    is a tiebreaker only: a STRATEGIC project nudges an otherwise-internal
    deliverable toward CRITICAL_DECISION review when its title hints at a
    decision. Pure function — never raises, never does I/O.
    """
    if action_type:
        mapped = _ACTION_TYPE_CLASS.get(action_type.strip().lower())
        if mapped is not None:
            return mapped

    title = (task_title or "").lower()
    for cls, keywords in _CLASS_KEYWORDS:
        if any(kw in title for kw in keywords):
            return cls

    # Tiebreaker: a strategic project producing a "decision"/"plan"/"strategy"
    # artifact is outward-consequential even without a louder keyword.
    if project_type and project_type.strip().lower() == "strategic":
        if any(
            hint in title
            for hint in ("decision", "strategy", "plan to", "commit to")
        ):
            return DeliverableClass.CRITICAL_DECISION

    return DeliverableClass.INTERNAL


def needs_csuite_review(cls: DeliverableClass) -> bool:
    """True for every outward class; False only for ``INTERNAL``."""
    return cls is not DeliverableClass.INTERNAL


def review_roles_for(cls: DeliverableClass) -> list[str]:
    """Reviewer roles for a class (copy; empty for ``INTERNAL``)."""
    return list(REVIEW_ROLES.get(cls, []))


__all__ = [
    "DeliverableClass",
    "REVIEW_ROLES",
    "classify_deliverable",
    "needs_csuite_review",
    "review_roles_for",
]
