"""CEO agent — the conductor of Kompany."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kompany.agents.base import BaseAgent
from kompany.core.directive import DecisionType


class AnswerResponse(BaseModel):
    """Structured output from :meth:`CEOAgent.answer`.

    ``has_proposal`` signals that the reply contains an actionable proposal the
    founder can execute by clicking GO. When true, ``proposal_directive`` holds
    the imperative directive text the engine will classify → execute on GO
    (e.g. "Launch a 72-hour presale sprint targeting the 10 warmest prospects").
    """
    text: str = Field(description="The answer / proposal shown to the founder. ~120 words max.")
    has_proposal: bool = Field(
        default=False,
        description=(
            "True when the reply contains a concrete, actionable proposal the founder can "
            "approve by clicking GO. False for pure informational answers (status, balance, etc.)."
        ),
    )
    proposal_directive: str = Field(
        default="",
        description=(
            "Imperative directive text to execute when the founder clicks GO. "
            "Required when has_proposal=true; empty string otherwise. "
            "Must be self-contained — the engine runs classify → execute on this text."
        ),
    )


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
    # Decision-quality routing (ADR-0006). Best-effort tag of WHAT KIND of
    # strategic decision this is, so the debate engine can convene the right
    # executives and retrieve relevant precedents. None / "general" when it
    # doesn't fit a specific bucket; only meaningful for strategic+debate.
    decision_type: DecisionType | None = Field(
        default=None,
        description=(
            "go_to_market|pricing|hiring|infra|legal|fundraising|"
            "product_scope|general — best-effort; null/general if unclear"
        ),
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
        recent_context: str | None = None,
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
        context_parts_classify = []
        if recent_context:
            context_parts_classify.append(
                f"Recent conversation history (across sessions):\n{recent_context}"
            )
        if session_context:
            context_parts_classify.append(
                f"Conversation so far (this session only):\n{session_context}"
            )
        context_block = (
            ("\n\n".join(context_parts_classify) + "\n\n")
            if context_parts_classify
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
            f"For approval_tier: auto (<€5, research), ceo (€5-50), master (>€50 or irreversible)\n\n"
            f"For decision_type (best-effort — only meaningful for STRATEGIC "
            f"decisions): tag WHAT KIND of decision this is so the right "
            f"executives can be convened:\n"
            f"- go_to_market: launch/channel/positioning/audience decisions\n"
            f"- pricing: price points, packaging, discounts, monetization\n"
            f"- hiring: adding/structuring the team or roles\n"
            f"- infra: architecture, tooling, platform, build-vs-buy\n"
            f"- legal: contracts, compliance, IP, regulatory\n"
            f"- fundraising: raising capital, runway, investor strategy\n"
            f"- product_scope: what to build / cut / prioritize\n"
            f"- general: strategic but none of the above\n"
            f"Use null or general when it does not clearly fit a bucket."
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
        recent_context: str | None = None,
        directive_id: str | None = None,
    ) -> "LLMResponse":  # noqa: F821 — runtime type from base.call_structured
        """Answer the founder's question grounded in real company state.

        Returns a structured :class:`AnswerResponse` via ``call_structured``.
        ``resp.parsed`` is the validated ``AnswerResponse``; ``resp.text`` is
        the raw JSON (not shown to the founder — callers use ``parsed.text``).

        ``session_context`` — prior turns of THIS session (within-session coherence).
        ``recent_context``  — last N turns across ALL sessions (cross-session context
        so short follow-ups like "批准" / "继续" are understood in new sessions).
        """
        context_parts = []
        if recent_context:
            context_parts.append(
                f"Recent conversation history (across sessions):\n{recent_context}"
            )
        if session_context:
            context_parts.append(
                f"Conversation so far (this session):\n{session_context}"
            )
        context_block = ("\n\n".join(context_parts) + "\n\n") if context_parts else ""

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
            "- Prefer the authoritative targets summary over any empty goal/time_horizon fields elsewhere.\n\n"
            "Output contract (STRICT — the founder reads results, not essays):\n"
            "- ``text``: first line IS the answer or proposal. No preamble, no headers, no 'CEO Analysis:' banner. Hard cap ~120 words. Bullets over prose. NEVER explain reasoning.\n"
            "- If the founder asks what to do next: text = team's AGREED, FINAL proposal — (1) single recommended move, (2) steps with owner agent + cost + deadline, (3) what the founder must approve/connect/decide.\n"
            "- Never restate balance/targets the founder can already see unless directly asked.\n"
            "- ``has_proposal``: true ONLY when your reply contains a concrete, actionable proposal the founder could approve and execute (creates projects / dispatches agents). False for pure info (status, balance, timeline queries).\n"
            "- ``proposal_directive``: when has_proposal=true, write a short imperative directive the engine will classify and execute on GO (e.g. 'Launch presale sprint: CRO targets 10 warm prospects in 24h, CMO rewrites offer copy, CPO defines MVP deliverable'). Empty string when has_proposal=false."
        )
        return self.call_structured(
            prompt=prompt,
            output_schema=AnswerResponse,
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
