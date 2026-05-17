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

    def classify(self, raw_input: str, directive_id: str | None = None) -> DirectiveClassification:
        """Classify a raw directive from the Master."""
        prompt = (
            f"The Master has given this directive:\n\n"
            f'"{raw_input}"\n\n'
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
