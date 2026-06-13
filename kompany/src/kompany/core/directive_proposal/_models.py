"""Pydantic models and prompt templates for directive proposal."""

from __future__ import annotations

from pydantic import BaseModel, Field


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


PROMPT_TEMPLATE = """The founder has just agreed to these targets for the company:

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


DISCUSSION_PROMPT_TEMPLATE = """You proposed these first-week directives to the founder:

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
