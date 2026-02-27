"""KompanyEngine — the single entry point for all interfaces."""

from __future__ import annotations

from pathlib import Path

from kompany.agents.registry import AgentRegistry
from kompany.config.settings import KompanySettings
from kompany.core.autonomy import AutonomyGate
from kompany.core.directive import (
    Directive,
    DirectiveResult,
    DirectiveStatus,
    DirectiveType,
)
from kompany.llm.client import LLMClient
from kompany.llm.cost_tracker import CostTracker
from kompany.state.database import Database
from kompany.state.journal import Journal
from kompany.state.ledger import Ledger
from kompany.state.models import (
    CompanySnapshot,
    LedgerCategory,
    Project,
    ProjectType,
)
from kompany.state.projects import Projects
from kompany.state.memory import AgentMemory


class KompanyEngine:
    """Core engine. All interfaces (CLI, API, MCP, SDK) call this."""

    def __init__(self, config_path: str | None = None):
        self.settings = KompanySettings.load(config_path)
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.settings.data_dir)
        self.ledger = Ledger(self.db)
        self.journal = Journal(self.db)
        self.projects = Projects(self.db)
        self.memory = AgentMemory(self.db)
        self.cost_tracker = CostTracker(self.ledger)
        self.autonomy = AutonomyGate()

        self.llm = LLMClient(
            settings=self.settings,
            cost_tracker=self.cost_tracker,
        )
        self.registry = AgentRegistry(
            self.llm, self.settings, self.ledger, self.projects
        )

    def get_company_state(self) -> dict:
        """Get current company state for agent context."""
        return {
            "name": self.settings.company_name,
            "product": self.settings.company_product,
            "stage": self.settings.company_stage,
            "balance": self.ledger.get_balance(),
            "active_projects": self.projects.count_active(),
        }

    def initialize_company(
        self, name: str, product: str, balance: float, stage: str = "solo"
    ) -> None:
        """Initialize a new Kompany with starting balance."""
        self.settings.company_name = name
        self.settings.company_product = product
        self.settings.company_stage = stage
        # Record initial capital
        if balance > 0:
            self.ledger.record(
                amount=balance,
                description=f"Initial capital for {name}",
                category=LedgerCategory.INCOME,
                approved_by="master",
            )

    def execute_project(self, project_id: str) -> dict:
        """Execute a revenue project's tasks autonomously."""
        from kompany.core.runner import ProjectRunner
        runner = ProjectRunner(self)
        result = runner.run(project_id)
        return result.model_dump()

    def process_directive(self, raw_input: str) -> DirectiveResult:
        """Main entry point. Takes natural language, returns result."""
        directive = Directive(raw_input=raw_input)
        state = self.get_company_state()

        # 1. CEO classifies
        ceo = self.registry.get("ceo", company_state=state)
        classification = ceo.classify(raw_input, directive_id=directive.id)

        directive.directive_type = DirectiveType(classification.directive_type)
        directive.assigned_squad = classification.primary_squad
        directive.assigned_agents = classification.agents_needed
        directive.requires_approval = classification.approval_tier
        directive.budget_required = classification.estimated_cost_eur
        directive.budget_available = self.ledger.get_balance()

        # 2. Route based on type
        handler = {
            DirectiveType.ACQUISITION: self._handle_acquisition,
            DirectiveType.STRATEGIC: self._handle_strategic,
            DirectiveType.OPERATIONAL: self._handle_operational,
            DirectiveType.INFORMATIONAL: self._handle_informational,
        }.get(directive.directive_type, self._handle_operational)

        return handler(directive, classification, ceo)

    def _handle_acquisition(self, directive, classification, ceo) -> DirectiveResult:
        """Handle ACQUISITION directives — must deliver, never downgrade."""
        # CFO checks budget (mechanical, no LLM cost)
        cfo = self.registry.get("cfo")
        cost = classification.estimated_cost_eur or 0
        budget = cfo.check_budget(cost)

        if budget["sufficient"]:
            directive.status = DirectiveStatus.AWAITING_APPROVAL
            return DirectiveResult(
                directive=directive,
                status="awaiting_approval",
                message=(
                    f"Budget sufficient. Balance: €{budget['available']:.2f}, "
                    f"Cost: €{cost:.2f}. Approve purchase?"
                ),
                total_ai_cost=self.cost_tracker.session_total,
                agents_used=["ceo", "cfo"],
            )

        # MISSION INTEGRITY: budget insufficient → create revenue project
        shortfall = budget["shortfall"]
        current_balance = budget["available"]

        plan = ceo.create_revenue_plan(
            original_directive=directive.raw_input,
            target_amount=cost,
            current_balance=current_balance,
            shortfall=shortfall,
            directive_id=directive.id,
        )

        # Create the project in DB
        project = Project(
            name=f"Fund: {directive.raw_input[:50]}",
            type=ProjectType.REVENUE,
            target_amount=cost,
            funded_amount=current_balance,
            triggers_directive_id=directive.id,
            plan=plan.model_dump(),
            assigned_agents=["ceo", "cro", "cmo", "cto"],
        )
        self.projects.create(project)

        # Build response message
        paths_text = "\n".join(
            f"  {i+1}. {p.name} — €{p.estimated_revenue_eur:.0f} "
            f"({p.timeframe}, {p.risk_level} risk)"
            for i, p in enumerate(plan.paths)
        )
        msg = (
            f"Mission accepted: {directive.raw_input}\n\n"
            f"Cost: €{cost:.2f}\n"
            f"Balance: €{current_balance:.2f}\n"
            f"Shortfall: €{shortfall:.2f}\n\n"
            f"Revenue project created: {project.name}\n"
            f"Revenue paths:\n{paths_text}\n\n"
            f"Recommended: {plan.recommended_path}\n"
            f"Estimated timeframe: {plan.estimated_timeframe}\n\n"
            f"AI cost for this directive: ${self.cost_tracker.session_total:.4f}"
        )

        directive.status = DirectiveStatus.ACTIVE
        return DirectiveResult(
            directive=directive,
            status="revenue_project_created",
            message=msg,
            project_id=project.id,
            total_ai_cost=self.cost_tracker.session_total,
            agents_used=["ceo", "cfo"],
        )

    def _handle_strategic(self, directive, classification, ceo) -> DirectiveResult:
        """Handle STRATEGIC directives — full debate when classification requests it."""
        if classification and classification.requires_debate:
            return self._handle_strategic_debate(directive)

        # Simple CEO analysis for non-debate strategic questions
        resp = ceo.call(
            prompt=(
                f"The Master asks: \"{directive.raw_input}\"\n\n"
                f"As CEO, provide your strategic analysis and recommendation. "
                f"Consider financial, technical, and market perspectives."
            ),
            directive_id=directive.id,
        )
        directive.status = DirectiveStatus.COMPLETED
        return DirectiveResult(
            directive=directive,
            status="completed",
            message=f"CEO Analysis:\n\n{resp.text}",
            total_ai_cost=self.cost_tracker.session_total,
            agents_used=["ceo"],
        )

    def _handle_strategic_debate(self, directive) -> DirectiveResult:
        """Run a full multi-agent debate for a strategic directive."""
        from kompany.core.debate import DebateEngine

        stage = self.settings.company_stage or "solo"
        debate = DebateEngine(self.registry, stage=stage)
        state = self.get_company_state()
        result = debate.run(
            question=directive.raw_input,
            company_state=state,
            directive_id=directive.id,
        )

        # Format the debate result for the Master
        parts = [f"Debate: \"{directive.raw_input}\"\n"]

        for i, rnd in enumerate(result.rounds, 1):
            parts.append(f"--- Round {i} ---")
            for pos in rnd:
                parts.append(
                    f"[{pos.agent_name}] {pos.recommendation} "
                    f"(confidence: {pos.confidence})"
                )

        if result.synthesis:
            s = result.synthesis
            parts.append(f"\n--- CoS Synthesis ---")
            parts.append(f"Consensus: {s.consensus_position}")
            parts.append(f"Recommended: {s.recommended_option}")
            if s.risk_flags:
                parts.append(f"Risks: {', '.join(s.risk_flags)}")

        if result.decision:
            d = result.decision
            parts.append(f"\n--- CEO Decision ---")
            parts.append(f"Decision: {d.decision}")
            parts.append(f"Rationale: {d.rationale}")
            parts.append(f"Confidence: {d.confidence_score:.0%}")
            parts.append(f"Reversibility: {d.reversibility}")
            if d.next_steps:
                parts.append("Next steps:")
                for step in d.next_steps:
                    parts.append(f"  - {step}")

        parts.append(
            f"\nAI cost for this debate: ${self.cost_tracker.session_total:.4f}"
        )

        directive.status = DirectiveStatus.COMPLETED
        return DirectiveResult(
            directive=directive,
            status="completed",
            message="\n".join(parts),
            total_ai_cost=self.cost_tracker.session_total,
            agents_used=result.agents_participated + ["cos", "ceo"],
        )

    def _handle_operational(self, directive, classification, ceo) -> DirectiveResult:
        """Handle OPERATIONAL directives — direct delegation."""
        resp = ceo.call(
            prompt=(
                f"The Master's operational directive: \"{directive.raw_input}\"\n\n"
                f"Break this into concrete action steps and delegate."
            ),
            directive_id=directive.id,
        )
        directive.status = DirectiveStatus.COMPLETED
        return DirectiveResult(
            directive=directive,
            status="completed",
            message=f"CEO Delegation:\n\n{resp.text}",
            total_ai_cost=self.cost_tracker.session_total,
            agents_used=["ceo"],
        )

    def _handle_informational(self, directive, classification, ceo) -> DirectiveResult:
        """Handle INFORMATIONAL directives — query state, no LLM needed."""
        cfo = self.registry.get("cfo")
        summary = cfo.get_summary()
        active = self.projects.list_active()

        projects_text = ""
        if active:
            projects_text = "\n\nActive projects:\n" + "\n".join(
                f"  - {p.name} (€{p.funded_amount:.2f}/€{p.target_amount or 0:.2f})"
                for p in active
            )

        msg = (
            f"Company: {self.settings.company_name}\n"
            f"Balance: €{summary['balance']:.2f}\n"
            f"Total income: €{summary['total_income']:.2f}\n"
            f"Total expenses: €{summary['total_expenses']:.2f}\n"
            f"Total AI costs: ${abs(summary['total_ai_costs']):.4f}"
            f"{projects_text}"
        )

        directive.status = DirectiveStatus.COMPLETED
        return DirectiveResult(
            directive=directive,
            status="completed",
            message=msg,
            total_ai_cost=0,
            agents_used=["cfo"],
        )
