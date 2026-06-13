"""Team-proposes-the-plan directive proposal mixin — package root.

Implements the contract documented in
``docs/context/operations.md:60-62`` (and surfaced via the
``engineering-team-proposes-plan`` shared memory): when the founder
finishes onboarding with a template that ships no pre-staged
directives, the **team** designs the first batch — the founder picks
from the team's proposal, doesn't write it from scratch.

Lives as a ``KompanyEngine`` mixin so call sites stay unchanged
(per ADR-0003 the engine is being split into mixins). The mixin
adds two public methods:

    ``propose_first_directives()`` — read agreed_targets + company
    state, run a short LLM pass with the CEO, write 3 draft projects,
    return them. Idempotent: if drafts already exist with
    ``status='draft'``, return them without spending another LLM call.

    ``discuss_first_directives(question)`` — CEO follow-up Q&A on the
    current draft directives; may revise the plan in-place.

Sub-module layout
-----------------
_models.py       — Pydantic schemas + prompt templates
_helpers.py      — stateless helpers (provider name, dict coercion,
                   existing-draft query, heuristic seeds)
_persistence.py  — _filter_by_founder_rules, _persist_proposed_directives
_proposal.py     — propose_first_directives + _llm_first_directives
_discussion.py   — discuss_first_directives
"""

from __future__ import annotations

from ._discussion import _DirectiveDiscussionMixin
from ._helpers import _DirectiveProposalHelpersMixin
from ._persistence import _DirectiveProposalPersistenceMixin
from ._proposal import _DirectiveProposalMixin


class DirectiveProposalMixin(
    _DirectiveProposalHelpersMixin,
    _DirectiveProposalPersistenceMixin,
    _DirectiveProposalMixin,
    _DirectiveDiscussionMixin,
):
    """Composed ``KompanyEngine`` mixin — team-generated first-week directives.

    Import path is unchanged from the old single-file module::

        from kompany.core.directive_proposal import DirectiveProposalMixin
    """
