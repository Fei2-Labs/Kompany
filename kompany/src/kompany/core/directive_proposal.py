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
    """One first-week directive the team recommends.

    Includes the concrete week plan + success metric + cost estimate
    so the founder picks with enough information to decide. Bare
    title + one-line rationale is too abstract for a founder who
    doesn't know the domain (the whole point of paying for the AI
    team is they spell out the WHAT and HOW)."""

    title: str = Field(min_length=4, max_length=140)
    rationale: str = Field(min_length=10, max_length=600)
    proposer_role: str = Field(
        default="ceo",
        description="Which C-suite role led this proposal: ceo / cro / cpo / cmo / cfo.",
    )
    week_plan: list[str] = Field(
        default_factory=list,
        description=(
            "3-5 short bullets describing what gets done day-by-day. "
            "Concrete enough that the founder could start tomorrow."
        ),
    )
    success_metric: str = Field(
        default="",
        max_length=240,
        description=(
            "One sentence naming the measurable outcome that decides "
            "whether this week succeeded."
        ),
    )
    expected_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Rough USD spend this directive needs (ad spend, tools, "
            "subscriptions). 0.0 = no monetary cost beyond LLM tokens."
        ),
    )
    other_agents_involved: list[str] = Field(
        default_factory=list,
        description=(
            "Beyond the proposer, which other C-suite or subagent "
            "roles will collaborate. Lower-case role keys."
        ),
    )


class _ProposedDirectiveList(BaseModel):
    directives: list[_ProposedDirective] = Field(min_length=2, max_length=4)


class _DiscussionResponse(BaseModel):
    """CEO's reply to a founder's follow-up question.

    May optionally include a refined directive list; when present, the
    engine replaces the old drafts with the new ones."""

    answer: str = Field(min_length=10, max_length=2000)
    directives_changed: bool = False
    directives: list[_ProposedDirective] | None = None


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
ONE of to start. Each directive must include ALL fields below — bare
titles and one-line rationales are not enough for a founder who can't
evaluate the abstract idea against their own context.

Required fields per directive:

  - title:               single sentence, ≤ 140 chars, concrete (not "build
                         marketing strategy" — say "ship landing page +
                         capture 50 waitlist signups")
  - rationale:           why THIS move now, tied to budget + deadline
                         + agreed targets. 2-3 sentences.
  - proposer_role:       CEO / CRO / CPO / CMO / CFO
  - week_plan:           3-5 day-level bullets ("Mon: write copy",
                         "Tue: deploy", ...). Concrete enough to start
                         tomorrow.
  - success_metric:      one sentence naming the measurable outcome that
                         decides this week succeeded (e.g. "≥30 waitlist
                         signups", "5 customer interview write-ups",
                         "Stripe sandbox processes test charge").
  - expected_cost_usd:   rough USD spend beyond LLM tokens (ad spend,
                         tools, subscriptions). 0 if none.
  - other_agents_involved: lower-case list of OTHER roles collaborating
                         (e.g. ["cto", "cmo"]).

Make the three diverse: at least one cheap-fast validation move, one
move that pushes money toward the revenue target, one that reduces a
customer / ICP unknown.
"""


_DISCUSSION_PROMPT_TEMPLATE = """You proposed these first-week directives to the founder:

{existing_directives}

The founder is asking a follow-up question before picking:

  "{question}"

Reply with:

  - answer: 2-5 sentences directly addressing the founder's question.
    Speak as the CEO. Reference the current 3 directives when relevant.

  - directives_changed: true ONLY if the founder's question or new
    information genuinely changed your recommendation. Don't flip
    on a whim; the team already debated this.

  - directives: if (and only if) directives_changed == true, supply
    the NEW list of 2-4 directives in the same schema as before
    (title + rationale + proposer_role + week_plan + success_metric
    + expected_cost_usd + other_agents_involved). Otherwise omit.

Context — the founder's agreed targets:

  Initial budget   : ${initial_budget:,.2f} USD
  Revenue target   : ${revenue_target:,.2f} USD
  Customer target  : {customer_target}
  Deadline         : {deadline}
  Company goal     : {company_goal}
"""


class DirectiveProposalMixin:
    """``KompanyEngine`` mixin — team-generated first-week directives."""

    def propose_first_directives(
        self,
        *,
        skip_llm: bool | None = None,
        force_heuristic: bool = False,
    ) -> dict[str, Any]:
        """Read agreed_targets + company state, run a short CEO pass,
        write 3 draft projects, return a structured result. Idempotent.

        Returns a dict with shape::

            {
                "status": "ok" | "team_failed" | "no_targets" | "heuristic",
                "directives": [...],     # may be empty when status != ok
                "error_code": str|None,  # network / unauthorized /
                                         # rate_limited / provider_error
                                         # / unknown; only set on
                                         # team_failed
                "error_message": str|None,
                "provider": str|None,    # which provider was tried
            }

        Distinct from the previous silent-fallback shape — the caller
        (REST endpoint + onboarding UI) needs to know whether the
        AI actually proposed the directives or whether we fell through
        to generic seeds. Lying to the founder ("here's your AI's
        plan!") when the LLM never ran erodes trust. ``force_heuristic``
        explicit-opts into the local fallback (user clicked "use
        starter pack" on the error screen).

        ``skip_llm`` short-circuits to the heuristic without trying
        the LLM; defaults to True under ``KOMPANY_TEST_MODE=1`` so
        tests don't need a live API key.
        """
        import os

        existing = self._existing_draft_projects()
        if existing:
            return {
                "status": "ok",
                "directives": existing,
                "error_code": None,
                "error_message": None,
                "provider": None,
            }

        agreed = get_targets_state(self.db, "agreed")
        if agreed is None:
            return {
                "status": "no_targets",
                "directives": [],
                "error_code": "no_targets",
                "error_message": "Agreed targets not set; complete the team review first.",
                "provider": None,
            }

        if skip_llm is None:
            skip_llm = os.environ.get("KOMPANY_TEST_MODE", "") == "1"

        provider = self._active_provider_name()

        if skip_llm or force_heuristic:
            directives = self._heuristic_first_directives(agreed)
            persisted = self._persist_proposed_directives(
                directives, source="team_proposal_first_week_heuristic"
            )
            return {
                "status": "heuristic",
                "directives": persisted,
                "error_code": None,
                "error_message": None,
                "provider": provider,
            }

        try:
            directives = self._llm_first_directives(agreed)
        except Exception as exc:  # noqa: BLE001 — surfaced to UI
            from kompany.interfaces.api import _classify_ping_error

            detail = f"{type(exc).__name__}: {exc}"
            code = _classify_ping_error(detail)
            return {
                "status": "team_failed",
                "directives": [],
                "error_code": code,
                "error_message": detail,
                "provider": provider,
            }

        persisted = self._persist_proposed_directives(directives)
        return {
            "status": "ok",
            "directives": persisted,
            "error_code": None,
            "error_message": None,
            "provider": provider,
        }

    def discuss_first_directives(self, question: str) -> dict[str, Any]:
        """Founder follow-up Q&A on the current first-week directives.

        Loads agreed_targets + the current draft directives, runs ONE
        CEO LLM call, returns ``{ status, answer, directives_changed,
        directives, error_code, error_message, provider }``.

        When the CEO decides the question warrants a revised plan
        (``directives_changed=True``), the existing drafts are deleted
        and the new ones persisted with source
        ``team_proposal_first_week_revised`` so the timeline shows the
        founder's Q&A triggered the change.
        """
        provider = self._active_provider_name()

        question = (question or "").strip()
        if not question:
            return {
                "status": "team_failed",
                "answer": "",
                "directives_changed": False,
                "directives": [],
                "error_code": "empty_question",
                "error_message": "Question is empty.",
                "provider": provider,
            }

        agreed = get_targets_state(self.db, "agreed")
        if agreed is None:
            return {
                "status": "no_targets",
                "answer": "",
                "directives_changed": False,
                "directives": [],
                "error_code": "no_targets",
                "error_message": "Agreed targets not set; complete the team review first.",
                "provider": provider,
            }
        existing = self._existing_draft_projects()
        if not existing:
            return {
                "status": "no_directives",
                "answer": "",
                "directives_changed": False,
                "directives": [],
                "error_code": "no_directives",
                "error_message": "No draft directives to discuss yet.",
                "provider": provider,
            }

        try:
            ceo = self.registry.get(
                "ceo", company_state=self.get_company_state()
            )
            existing_block = "\n".join(
                f"  {i+1}. [{(d.get('proposer_role') or 'ceo').upper()}] "
                f"{d['name']} — {d.get('rationale','')}"
                for i, d in enumerate(existing)
            )
            prompt = _DISCUSSION_PROMPT_TEMPLATE.format(
                existing_directives=existing_block,
                question=question,
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
                output_schema=_DiscussionResponse,
                max_tokens=1200,
                action_type="first_directive_discussion",
            )
        except Exception as exc:  # noqa: BLE001
            from kompany.interfaces.api import _classify_ping_error

            detail = f"{type(exc).__name__}: {exc}"
            return {
                "status": "team_failed",
                "answer": "",
                "directives_changed": False,
                "directives": [],
                "error_code": _classify_ping_error(detail),
                "error_message": detail,
                "provider": provider,
            }

        parsed = getattr(resp, "parsed", None)
        answer = (getattr(parsed, "answer", "") or "").strip()
        changed = bool(getattr(parsed, "directives_changed", False))
        new_directives_raw = list(getattr(parsed, "directives", []) or [])

        if changed and new_directives_raw:
            # Replace the old drafts.
            self.db.execute("DELETE FROM projects WHERE status = 'draft'")
            self.db.commit()
            new_dicts = [self._directive_to_dict(d) for d in new_directives_raw[:3]]
            persisted = self._persist_proposed_directives(
                new_dicts, source="team_proposal_first_week_revised"
            )
        else:
            persisted = existing
            changed = False

        try:
            self.audit.record(
                event_type="first_directive_discussion",
                action="Founder asked a follow-up about first directives",
                detail={
                    "question": question[:200],
                    "directives_changed": changed,
                },
            )
        except Exception:  # noqa: BLE001 — best-effort
            pass

        return {
            "status": "ok",
            "answer": answer,
            "directives_changed": changed,
            "directives": persisted,
            "error_code": None,
            "error_message": None,
            "provider": provider,
        }

    def _active_provider_name(self) -> str:
        """Best-effort guess at which provider name is active for
        labelling errors. Reads vault-loaded settings; falls back to
        'unknown'. Display-only — never used for routing."""
        if getattr(self.settings, "custom_base_url", ""):
            return "custom"
        for name in ("anthropic", "openai", "gemini", "glm", "kimi"):
            attr = f"{name}_api_key"
            if getattr(self.settings, attr, ""):
                return name
        return "unknown"

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
            out.append(self._directive_to_dict(d))
        return out

    @staticmethod
    def _directive_to_dict(d) -> dict[str, Any]:
        """Coerce a ``_ProposedDirective`` Pydantic instance to the plain
        dict shape the rest of the engine + UI work with."""
        return {
            "title": getattr(d, "title", "") or "",
            "rationale": getattr(d, "rationale", "") or "",
            "proposer_role": (getattr(d, "proposer_role", "") or "ceo").lower(),
            "week_plan": list(getattr(d, "week_plan", []) or []),
            "success_metric": getattr(d, "success_metric", "") or "",
            "expected_cost_usd": float(getattr(d, "expected_cost_usd", 0.0) or 0.0),
            "other_agents_involved": [
                str(r).lower()
                for r in (getattr(d, "other_agents_involved", []) or [])
            ],
        }

    def _heuristic_first_directives(self, agreed) -> list[dict[str, Any]]:
        """Three safe defaults when LLM is unavailable / disabled.

        Each is intentionally generic enough to apply to any company
        shape; the LLM path is what produces founder-specific wording.
        Fields match the LLM schema 1:1 so the UI renders them
        identically — but the source marker upstream tells consumers
        these are starter-pack seeds, not real team output.
        """
        rev = float(agreed.revenue_target or 0)
        return [
            {
                "title": "Run 5 customer interviews to lock the ICP",
                "rationale": (
                    "Cheapest unknown-reducing move this week. Without "
                    "a sharp ICP, every later move spends money guessing. "
                    "5 calls reveal whether the founder's hypothesis "
                    "matches the market's pain."
                ),
                "proposer_role": "cpo",
                "week_plan": [
                    "Mon: CRO drafts a 4-question discovery script",
                    "Tue-Wed: founder books + runs 3 calls",
                    "Thu: runs 2 more calls; transcripts collected",
                    "Fri: CV writes a 1-page ICP synthesis",
                ],
                "success_metric": "5 interview write-ups + one ICP statement the team agrees with",
                "expected_cost_usd": 0.0,
                "other_agents_involved": ["cro", "cv"],
            },
            {
                "title": "Ship a landing page + waitlist (1 week)",
                "rationale": (
                    f"Concrete artefact starts moving toward the ${rev:,.0f} "
                    "revenue target. Even no purchases this week, a "
                    "waitlist gives the team an audience to test pricing + "
                    "messaging against in week 2."
                ),
                "proposer_role": "cmo",
                "week_plan": [
                    "Mon: CMO writes copy from ICP / value prop",
                    "Tue: CTO deploys static page (Vercel / Netlify)",
                    "Wed: CRO wires form → email capture",
                    "Thu: founder shares to 3 communities + LinkedIn",
                    "Fri: measure signup rate, log refusals",
                ],
                "success_metric": "≥ 30 waitlist signups OR a clear refusal pattern documented",
                "expected_cost_usd": 20.0,
                "other_agents_involved": ["cto", "cro"],
            },
            {
                "title": "Define pricing + payment rail (Stripe sandbox)",
                "rationale": (
                    "Clears the path for first paid customer without "
                    "committing real money. CFO sizes the unit economics "
                    "so pricing isn't pulled out of thin air."
                ),
                "proposer_role": "cro",
                "week_plan": [
                    "Mon: CFO models 3 pricing tiers vs cost-of-delivery",
                    "Tue: CRO picks recommended tier with rationale",
                    "Wed-Thu: CTO wires Stripe sandbox + test checkout",
                    "Fri: end-to-end dry run with founder as customer",
                ],
                "success_metric": "Stripe sandbox processes a test charge; pricing memo signed off",
                "expected_cost_usd": 0.0,
                "other_agents_involved": ["cfo", "cto"],
            },
        ]

    def _persist_proposed_directives(
        self,
        directives: list[dict[str, Any]],
        *,
        source: str = "team_proposal_first_week",
    ) -> list[dict[str, Any]]:
        """Write each proposed directive as a status='draft' project,
        then return ``[{id, name, type, status, rationale, proposer}]``
        rows the REST layer + UI can render directly.

        ``source`` distinguishes team-LLM directives from heuristic
        starter packs so downstream consumers (distillation, audit
        timeline) can tell them apart."""
        rows: list[dict[str, Any]] = []
        for d in directives[:3]:
            project = Project(
                name=d["title"][:120],
                type=ProjectType.OPERATIONAL,
                plan={
                    "suggested_directive": d["title"],
                    "rationale": d.get("rationale", ""),
                    "proposer_role": d.get("proposer_role", "ceo"),
                    "week_plan": d.get("week_plan", []) or [],
                    "success_metric": d.get("success_metric", ""),
                    "expected_cost_usd": float(d.get("expected_cost_usd", 0.0) or 0.0),
                    "other_agents_involved": d.get("other_agents_involved", []) or [],
                    "source": source,
                },
                assigned_agents=list(d.get("other_agents_involved", []) or []),
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
                "week_plan": d.get("week_plan", []) or [],
                "success_metric": d.get("success_metric", ""),
                "expected_cost_usd": float(d.get("expected_cost_usd", 0.0) or 0.0),
                "other_agents_involved": d.get("other_agents_involved", []) or [],
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
                "week_plan": plan.get("week_plan", []) or [],
                "success_metric": plan.get("success_metric", ""),
                "expected_cost_usd": float(plan.get("expected_cost_usd", 0.0) or 0.0),
                "other_agents_involved": plan.get("other_agents_involved", []) or [],
            })
        return out
