"""Team-proposes-the-plan directive proposal mixin.

Implements the contract documented in
``docs/context/operations.md:60-62`` (and surfaced via the
``engineering-team-proposes-plan`` shared memory): when the founder
finishes onboarding with a template that ships no pre-staged
directives, the **team** designs the first batch — the founder picks
from the team's proposal, doesn't write it from scratch.

Lives as a ``KompanyEngine`` mixin so call sites stay unchanged
(per ADR-0003 the engine is being split into mixins). The mixin
adds one public method:

    ``propose_first_directives()`` — read agreed_targets + company
    state, run a short LLM pass with the CEO, write 3 draft projects,
    return them. Idempotent: if drafts already exist with
    ``status='draft'``, return them without spending another LLM call.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from kompany.state.models import Project, ProjectType
from kompany.state.targets import get_state as get_targets_state


class _ProposedDirective(BaseModel):
    """One first-week directive the team recommends."""

    title: str = Field(min_length=4, max_length=140)
    rationale: str = Field(min_length=10, max_length=600)
    proposer_role: str = Field(
        default="ceo",
        description="Which C-suite role led this proposal: ceo / cro / cpo.",
    )


class _ProposedDirectiveList(BaseModel):
    directives: list[_ProposedDirective] = Field(min_length=2, max_length=4)


_PROMPT_TEMPLATE = """The founder has just agreed to these targets for the company:

  Initial budget   : ${initial_budget:,.2f} USD
  Revenue target   : ${revenue_target:,.2f} USD
  Customer target  : {customer_target}
  Deadline         : {deadline}

Company goal (founder's words, if any):
  {company_goal}

You are the CEO of this company. Your team (CRO for revenue, CPO for
product, CMO for distribution, CFO for cost discipline) has signed off
on the targets. **You are NOT asking the founder what to do next** —
they're a solo founder who hired you precisely so you'd design the plan.

Propose exactly THREE first-week directives the founder should pick
ONE of to start. Each directive must:

- be a concrete, single-week, single-owner action (not a quarter-long
  initiative);
- cite which C-suite role proposed it (CEO / CRO / CPO / CMO);
- give a one-sentence rationale tied to the agreed budget + deadline
  (e.g. "needed to validate ICP before week-2 spend" or "highest
  expected ROI per dollar this week");
- be diverse — at least one should be cheap/fast (validation), one
  should move money toward the revenue target, one should reduce
  unknowns about the customer.

Format as the structured ClaimList schema.
"""


class DirectiveProposalMixin:
    """``KompanyEngine`` mixin — team-generated first-week directives."""

    def propose_first_directives(
        self,
        *,
        skip_llm: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Read agreed_targets + company state, run a short CEO pass,
        write 3 draft projects, return them as dicts. Idempotent.

        ``skip_llm`` short-circuits to a heuristic when set; defaults
        to True under ``KOMPANY_TEST_MODE=1`` so tests don't need a
        live API key. Production runs always invoke the LLM.
        """
        import os

        existing = self._existing_draft_projects()
        if existing:
            return existing

        agreed = get_targets_state(self.db, "agreed")
        if agreed is None:
            # No agreed targets — caller is upstream of the
            # feasibility-review flow. Return empty so the UI shows
            # the bare-text fallback rather than fabricating a plan
            # off zero context.
            return []

        if skip_llm is None:
            skip_llm = os.environ.get("KOMPANY_TEST_MODE", "") == "1"

        directives: list[dict[str, Any]] = []
        if skip_llm:
            directives = self._heuristic_first_directives(agreed)
        else:
            try:
                directives = self._llm_first_directives(agreed)
            except Exception as exc:  # noqa: BLE001
                # Never block onboarding because the LLM hiccuped.
                # Fall back to the heuristic so the founder always sees
                # at least three concrete cards.
                directives = self._heuristic_first_directives(agreed)
                directives.append({
                    "title": "(LLM proposal failed — heuristic used)",
                    "rationale": f"Provider error: {exc}",
                    "proposer_role": "ceo",
                    "_heuristic_fallback": True,
                })

        return self._persist_proposed_directives(directives)

    # ------------------------------------------------------------------
    # Internal — LLM, heuristic, persistence
    # ------------------------------------------------------------------

    def _llm_first_directives(self, agreed) -> list[dict[str, Any]]:
        from kompany.core.debate import CLAIMS_SCHEMA_HINT  # noqa: F401

        ceo = self.registry.get(
            "ceo", company_state=self.get_company_state()
        )
        prompt = _PROMPT_TEMPLATE.format(
            initial_budget=float(agreed.initial_budget or 0),
            revenue_target=float(agreed.revenue_target or 0),
            customer_target=(
                "not set"
                if agreed.customer_target is None
                else str(agreed.customer_target)
            ),
            deadline=str(agreed.deadline or "not set"),
            company_goal=self.settings.company_goal or "(none provided)",
        )
        resp = ceo.call_structured(
            prompt=prompt,
            output_schema=_ProposedDirectiveList,
            max_tokens=900,
            action_type="first_directive_proposal",
        )
        parsed = getattr(resp, "parsed", None)
        items = list(getattr(parsed, "directives", []) or [])
        if not items:
            raise ValueError("LLM returned zero directives")
        out: list[dict[str, Any]] = []
        for d in items[:3]:
            out.append({
                "title": d.title,
                "rationale": d.rationale,
                "proposer_role": (d.proposer_role or "ceo").lower(),
            })
        return out

    def _heuristic_first_directives(self, agreed) -> list[dict[str, Any]]:
        """Three safe defaults when LLM is unavailable / disabled.

        Each is intentionally generic enough to apply to any company
        shape; the LLM path is what produces founder-specific wording.
        """
        rev = float(agreed.revenue_target or 0)
        return [
            {
                "title": "Run 5 customer interviews to lock the ICP",
                "rationale": (
                    "Cheapest unknown-reducing move this week. CV asks "
                    "every interviewee the same 4 questions; CRO writes "
                    "the discovery script. ~$0 in tools, ~5 hours."
                ),
                "proposer_role": "cpo",
            },
            {
                "title": "Ship a landing page + waitlist (1 week)",
                "rationale": (
                    "CMO writes copy from the ICP, CTO deploys, CRO sets "
                    "up the lead-capture funnel. Concrete artefact that "
                    f"starts moving toward the ${rev:,.0f} revenue target."
                ),
                "proposer_role": "cmo",
            },
            {
                "title": "Define pricing + payment rail (Stripe sandbox)",
                "rationale": (
                    "CFO sizes the unit economics, CRO picks the pricing "
                    "tiers, CTO wires the Stripe sandbox. No real money "
                    "moves yet — clears the path for first paid customer."
                ),
                "proposer_role": "cro",
            },
        ]

    def _persist_proposed_directives(
        self,
        directives: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Write each proposed directive as a status='draft' project,
        then return ``[{id, name, type, status, rationale, proposer}]``
        rows the REST layer + UI can render directly."""
        rows: list[dict[str, Any]] = []
        for d in directives[:3]:
            project = Project(
                name=d["title"][:120],
                type=ProjectType.OPERATIONAL,
                plan={
                    "suggested_directive": d["title"],
                    "rationale": d.get("rationale", ""),
                    "proposer_role": d.get("proposer_role", "ceo"),
                    "source": "team_proposal_first_week",
                },
                assigned_agents=[],
            )
            # Reuse the Templates helper's raw insert so the draft row
            # ends up with status='draft' just like template-staged
            # directives. _insert_draft_project lives on the Templates
            # service.
            self.templates._insert_draft_project(project, d["title"])
            rows.append({
                "id": project.id,
                "name": project.name,
                "type": project.type.value,
                "status": "draft",
                "rationale": d.get("rationale", ""),
                "proposer_role": d.get("proposer_role", "ceo"),
            })

        # Audit event so the action is visible in the timeline.
        try:
            self.audit.record(
                event_type="first_directive_proposal",
                action=f"Team proposed {len(rows)} first-week directives",
                detail={
                    "count": len(rows),
                    "proposers": [r.get("proposer_role") for r in rows],
                },
            )
        except Exception:  # noqa: BLE001 — best-effort
            pass

        return rows

    def _existing_draft_projects(self) -> list[dict[str, Any]]:
        """Return drafts already in the DB so the call is idempotent."""
        rows = self.db.execute(
            "SELECT id, name, type, plan "
            "FROM projects "
            "WHERE status = 'draft' "
            "ORDER BY created_at"
        ).fetchall()
        if not rows:
            return []
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                plan = json.loads(r["plan"] or "{}")
            except (TypeError, json.JSONDecodeError):
                plan = {}
            out.append({
                "id": r["id"],
                "name": r["name"],
                "type": r["type"],
                "status": "draft",
                "rationale": plan.get("rationale", ""),
                "proposer_role": plan.get("proposer_role", ""),
            })
        return out
