"""Per-type directive handlers (acquisition/strategic/operational/info).

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations


from kompany.core.directive import DirectiveResult, DirectiveStatus
from kompany.state.models import Project, ProjectType



class DirectiveHandlersMixin:
    def _handle_acquisition(self, directive, classification, ceo) -> DirectiveResult:
        """Handle ACQUISITION directives — must deliver, never downgrade."""
        # CFO checks budget (mechanical, no LLM cost)
        cfo = self.registry.get("cfo")
        cost = classification.estimated_cost_eur or 0
        budget = cfo.check_budget(cost)

        if budget["sufficient"]:
            can_auto_proceed = self.autonomy.check(
                directive.requires_approval or "master",
                cost,
            )
            approval_id = self._record_autonomy_result(directive, can_auto_proceed, cost)
            directive.status = (
                DirectiveStatus.ACTIVE
                if can_auto_proceed
                else DirectiveStatus.AWAITING_APPROVAL
            )
            return DirectiveResult(
                directive=directive,
                status="approved_for_execution" if can_auto_proceed else "awaiting_approval",
                message=(
                    f"Budget sufficient. Balance: €{budget['available']:.2f}, "
                    f"Cost: €{cost:.2f}. "
                    + (
                        "AutonomyGate approved execution."
                        if can_auto_proceed
                        else "Awaiting user approval through AutonomyGate."
                    )
                ),
                approval_id=approval_id,
                total_ai_cost=self.cost_tracker.run_total(),
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
            f"AI cost for this directive: ${self.cost_tracker.run_total():.4f}"
        )

        directive.status = DirectiveStatus.ACTIVE
        return DirectiveResult(
            directive=directive,
            status="revenue_project_created",
            message=msg,
            project_id=project.id,
            total_ai_cost=self.cost_tracker.run_total(),
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
        can_auto_proceed = self.autonomy.check(
            directive.requires_approval or "master",
            directive.budget_required,
        )
        approval_id = self._record_autonomy_result(
            directive,
            can_auto_proceed,
            directive.budget_required,
        )
        directive.status = (
            DirectiveStatus.COMPLETED
            if can_auto_proceed
            else DirectiveStatus.AWAITING_APPROVAL
        )
        return DirectiveResult(
            directive=directive,
            status="completed" if can_auto_proceed else "awaiting_approval",
            message=(
                f"CEO Analysis:\n\n{resp.text}"
                if can_auto_proceed
                else f"CEO Recommendation (awaiting approval):\n\n{resp.text}"
            ),
            approval_id=approval_id,
            total_ai_cost=self.cost_tracker.run_total(),
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

        # Persist the structured debate transcript so episodes / future
        # "why did we decide that?" tooling can replay it. The formatted
        # text below is still returned as ``message`` for backward
        # compatibility with existing callers.
        debate_id = self.debates.record(
            rounds=result.rounds,
            synthesis=result.synthesis,
            decision=result.decision,
            directive_id=directive.id,
            project_id=None,
        )
        self.audit.record(
            "debate.recorded",
            "Strategic debate transcript persisted",
            detail={
                "debate_id": debate_id,
                "agents_participated": result.agents_participated,
                "rounds": len(result.rounds),
            },
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
            f"\nAI cost for this debate: ${self.cost_tracker.run_total():.4f}"
        )

        can_auto_proceed = self.autonomy.check(
            directive.requires_approval or "master",
            directive.budget_required,
        )
        approval_id = self._record_autonomy_result(
            directive,
            can_auto_proceed,
            directive.budget_required,
        )
        directive.status = (
            DirectiveStatus.COMPLETED
            if can_auto_proceed
            else DirectiveStatus.AWAITING_APPROVAL
        )
        return DirectiveResult(
            directive=directive,
            status="completed" if can_auto_proceed else "awaiting_approval",
            message="\n".join(parts),
            approval_id=approval_id,
            debate_id=debate_id,
            total_ai_cost=self.cost_tracker.run_total(),
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
        can_auto_proceed = self.autonomy.check(
            directive.requires_approval or "master",
            directive.budget_required,
        )
        approval_id = self._record_autonomy_result(
            directive,
            can_auto_proceed,
            directive.budget_required,
        )
        directive.status = (
            DirectiveStatus.COMPLETED
            if can_auto_proceed
            else DirectiveStatus.AWAITING_APPROVAL
        )
        return DirectiveResult(
            directive=directive,
            status="completed" if can_auto_proceed else "awaiting_approval",
            message=(
                f"CEO Delegation:\n\n{resp.text}"
                if can_auto_proceed
                else f"CEO Delegation Plan (awaiting approval):\n\n{resp.text}"
            ),
            approval_id=approval_id,
            total_ai_cost=self.cost_tracker.run_total(),
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
