"""CEO agent — the conductor of Kompany."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kompany.agents.base import BaseAgent


class DirectiveClassification(BaseModel):
    """CEO's classification of a raw directive."""
    directive_type: str = Field(description="acquisition|strategic|operational|informational")
    urgency: str = Field(default="standard", description="immediate|standard|background")
    reasoning: str
    estimated_cost_eur: float | None = None
    requires_debate: bool = False
    primary_squad: str = Field(description="strategy|product|growth")
    agents_needed: list[str] = Field(default_factory=list)
    approval_tier: str = Field(description="auto|ceo|master")
    execution_plan: str = ""
    # CEO-channel routing (06-03-ceo-channel). The conductor auto-detects how
    # to handle each founder message: ``execute`` (clear intent → dispatch the
    # pipeline) / ``clarify`` (ambiguous → ask back with ONE concrete
    # question) / ``answer`` (a pure question → reply only, no work).
    # ``informational`` maps to ``answer``; ``directive_type`` is kept for
    # backward compat with the existing handler routing.
    route: str = Field(
        default="execute",
        description="execute|clarify|answer",
    )
    clarify_question: str = Field(
        default="",
        description="when route=clarify, the ONE concrete question to ask back",
    )


class RevenuePath(BaseModel):
    name: str
    description: str
    estimated_revenue_eur: float
    timeframe: str
    risk_level: str = Field(description="low|medium|high")
    assigned_agent: str = ""


class RevenueProjectPlan(BaseModel):
    """CEO's plan to earn funds for an acquisition."""
    summary: str
    paths: list[RevenuePath]
    recommended_path: str
    total_estimated_revenue: float
    estimated_timeframe: str


_CEO_SYSTEM = """You are the CEO of {company_name}, an autonomous business operating system.

Your role is CONDUCTOR — you interpret the Master's (founder's) directives, classify them,
route them to the right team members, and ensure missions are completed.

CORE PRINCIPLE — MISSION INTEGRITY:
You NEVER downgrade the Master's mission. If resources are insufficient, you find a way
to acquire those resources. "We can't afford it" is not an answer. "Here's how we'll fund it" is.

Company state:
- Balance: €{balance}
- Active projects: {active_projects}
- Stage: {stage}

You are decisive, strategic, and action-oriented. You think in terms of execution, not advice."""


class CEOAgent(BaseAgent):
    """CEO — the conductor of Kompany."""

    role = "ceo"
    display_name = "CEO"
    model_tier = "apex"
    squad = "strategy"

    def __init__(self, llm, settings, company_state: dict | None = None):
        super().__init__(llm, settings)
        self._company_state = company_state or {}

    def system_prompt(self) -> str:
        prompt = _CEO_SYSTEM.format(
            company_name=self._company_state.get("name", "Kompany"),
            balance=self._company_state.get("balance", 0),
            active_projects=self._company_state.get("active_projects", 0),
            stage=self._company_state.get("stage", "solo"),
        )
        return self.with_soul_context(prompt)

    def classify(
        self,
        raw_input: str,
        directive_id: str | None = None,
        targets_summary: str | None = None,
        glossary_summary: str | None = None,
        session_context: str | None = None,
        clarify_capped: bool = False,
    ) -> DirectiveClassification:
        """Classify a raw directive from the Master.

        ``targets_summary`` is a one-paragraph render of the agreed
        company targets (revenue / customer / deadline). The engine
        injects it so CEO classification can weigh budget asks against
        the explicit revenue goal — produced by
        :meth:`kompany.core.engine.KompanyEngine._compose_targets_summary`.

        ``glossary_summary`` is a multi-line render of the company
        glossary (canonical term + forbidden synonyms) so the CEO's
        classification text adopts founder-defined terminology. Produced
        by :meth:`kompany.core.engine.KompanyEngine._compose_glossary_summary`.
        Empty string when no glossary is configured.
        """
        glossary_block = (
            f"{glossary_summary}\n\n" if glossary_summary else ""
        )
        target_block = (
            f"{targets_summary}\n\n" if targets_summary else ""
        )
        # CEO-channel session context: when continuing a conversation, inject
        # the prior turns of THIS session only (session-scoped per Decision 2)
        # so a clarify reply is judged against the question that was asked.
        context_block = (
            f"Conversation so far (this session only):\n{session_context}\n\n"
            if session_context
            else ""
        )
        # Routing guidance. At the clarify cap the conductor may no longer ask
        # another question — it must commit to execute or answer (engine
        # hard-enforces this too, but tell the model so it picks the right one).
        if clarify_capped:
            route_block = (
                "ROUTING (clarify limit reached — you MUST commit now):\n"
                "- You have already asked the maximum number of clarifying "
                "questions. Do NOT choose 'clarify'.\n"
                "- route=answer if this is a question; route=execute otherwise.\n\n"
            )
        else:
            route_block = (
                "ROUTING — set 'route' to exactly one of:\n"
                "- execute: intent is clear enough to dispatch the team now.\n"
                "- clarify: intent is AMBIGUOUS — set clarify_question to ONE "
                "concrete question that would unblock execution (grill-style "
                "requirements discovery). Use sparingly.\n"
                "- answer: this is a pure question (status/balance/runway/"
                "how-to) needing a reply only, no work. INFORMATIONAL → answer.\n\n"
            )
        prompt = (
            f"{glossary_block}"
            f"{target_block}"
            f"{context_block}"
            f"The Master has given this directive:\n\n"
            f'"{raw_input}"\n\n'
            f"{route_block}"
            f"Classify this directive. Consider:\n"
            f"- ACQUISITION: buying, getting, hiring something specific\n"
            f"- STRATEGIC: questions about direction, approach, should-we decisions\n"
            f"- OPERATIONAL: setting up, configuring, creating something internal\n"
            f"- INFORMATIONAL: asking about status, balance, runway, progress\n\n"
            f"For approval_tier: auto (<€5, research), ceo (€5-50), master (>€50 or irreversible)"
        )
        resp = self.call_structured(
            prompt=prompt,
            output_schema=DirectiveClassification,
            directive_id=directive_id,
        )
        return resp.parsed

    def answer(
        self,
        question: str,
        company_context: str,
        session_context: str | None = None,
        directive_id: str | None = None,
    ) -> "LLMResponse":  # noqa: F821 — runtime type from base.call
        """Answer the founder's question grounded in real company state.

        CEO-channel ``answer`` route (06-03-ceo-channel PR7). Unlike
        :meth:`classify` this is a freeform :meth:`call` — the conductor
        actually reads the founder's question and replies, grounded ONLY in
        ``company_context`` (a bounded snapshot of financials / active
        projects+tasks / staff activity assembled by the engine). It must not
        invent numbers; if the context lacks the answer it says so plainly.

        ``session_context`` is the prior turns of THIS session so follow-up
        questions in the same conversation stay coherent. The freeform call
        returns an :class:`~kompany.llm.client.LLMResponse` whose ``.text``
        is the reply and ``.cost_usd`` feeds the ledger (cost-as-expense).
        """
        context_block = (
            f"Conversation so far (this session only):\n{session_context}\n\n"
            if session_context
            else ""
        )
        prompt = (
            f"{context_block}"
            "The Master (founder) has asked you a question. Answer it directly "
            "and concisely as the conductor of the company.\n\n"
            f'Question:\n"{question}"\n\n'
            "Company context (the ONLY source of truth — do not invent numbers, "
            "names, or facts beyond what is here):\n"
            "----------------------------------------\n"
            f"{company_context}\n"
            "----------------------------------------\n\n"
            "Answer the founder's actual question using this context. Be factual "
            "and brief. If the context does not contain what is needed to answer, "
            "say so plainly rather than guessing.\n\n"
            "Important answer behavior:\n"
            "- Treat ACTIVE WORK NOW, RECENT COMPLETED WORK, and MISSION / TARGETS CURRENTLY SET as separate signals.\n"
            "- If active work count is zero, say that plainly — but do NOT imply the company has done nothing. When relevant, also mention RECENT COMPLETED WORK and the current mission/targets.\n"
            "- If mission/targets are present, do NOT imply they are missing or lost.\n"
            "- If the founder asks how to change or re-specify targets, point to the exact path shown in the context. If targets are missing, say they should be set there; if already present, say they can be revised there.\n"
            "- Prefer the authoritative targets summary over any empty goal/time_horizon fields elsewhere."
        )
        return self.call(
            prompt=prompt,
            directive_id=directive_id,
            action_type="ceo.answer",
        )

    def create_revenue_plan(
        self,
        original_directive: str,
        target_amount: float,
        current_balance: float,
        shortfall: float,
        directive_id: str | None = None,
    ) -> RevenueProjectPlan:
        """Create a revenue plan to fund an acquisition."""
        prompt = (
            f"The Master's directive: \"{original_directive}\"\n\n"
            f"Target cost: €{target_amount:.2f}\n"
            f"Current balance: €{current_balance:.2f}\n"
            f"Shortfall: €{shortfall:.2f}\n\n"
            f"MISSION INTEGRITY: Do NOT suggest downgrading the mission.\n"
            f"Create a revenue plan with 2-4 realistic paths to earn €{shortfall:.2f}.\n"
            f"Consider: consulting, freelance work, micro-SaaS, selling services, "
            f"digital products, or other revenue streams the founder could pursue."
        )
        resp = self.call_structured(
            prompt=prompt,
            output_schema=RevenueProjectPlan,
            directive_id=directive_id,
        )
        return resp.parsed
