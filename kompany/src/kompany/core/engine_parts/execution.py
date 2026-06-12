"""Project execution, decision packets, delivery release.

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations


from kompany.core.directive import Directive
from kompany.core.run_context import current_run_id, run_scope
from kompany.state.models import CLevelReview, ApprovalRequest, ApprovalStatus, CEOApprovalPacket, COOExecutionPlan, DecisionChainPacket, DecisionSynthesis, DeliveryPackage, ExecutionReport, FinancialEvaluation, ProjectStatus, RevenueProposal



class ProjectExecutionMixin:
    def execute_project(self, project_id: str) -> dict:
        """Execute a revenue project's tasks autonomously."""
        if current_run_id() is None:
            with run_scope():
                return self._execute_project_inner(project_id)
        return self._execute_project_inner(project_id)

    def _execute_project_inner(self, project_id: str) -> dict:
        rt = self.runtime.get()
        if rt["state"] == "suspended":
            self.audit.record(
                "runner.suspended_skip",
                "Skipped project execution: runtime suspended",
                detail={"project_id": project_id, "reason": rt["reason"]},
                project_id=project_id,
            )
            return {
                "status": "suspended",
                "project_id": project_id,
                "reason": rt["reason"],
                "since": rt["since"],
            }
        from kompany.core.runner import ProjectRunner
        runner = ProjectRunner(self)
        result = runner.run(project_id)
        return result.model_dump()

    def resume_project(self, project_id: str) -> dict:
        """Resume a project from persisted task/checkpoint state."""
        if current_run_id() is None:
            with run_scope():
                return self._resume_project_inner(project_id)
        return self._resume_project_inner(project_id)

    def _resume_project_inner(self, project_id: str) -> dict:
        latest = self.checkpoints.latest(project_id)
        rt = self.runtime.get()
        if rt["state"] == "suspended":
            self.audit.record(
                "runner.resume_suspended_skip",
                "Skipped project resume: runtime suspended",
                detail={
                    "project_id": project_id,
                    "reason": rt["reason"],
                    "checkpoint_id": latest["id"] if latest else None,
                },
                project_id=project_id,
            )
            return {
                "status": "suspended",
                "project_id": project_id,
                "reason": rt["reason"],
                "since": rt["since"],
                "latest_checkpoint": latest,
            }
        from kompany.core.runner import ProjectRunner
        runner = ProjectRunner(self)
        result = runner.resume(project_id).model_dump()
        return {
            "status": "resumed",
            "latest_checkpoint": latest,
            **result,
        }

    def prepare_decision_packet(
        self,
        raw_input: str,
        target_amount: float | None = None,
    ) -> dict:
        """Prepare a full executive decision-chain packet without executing it."""
        if current_run_id() is None:
            with run_scope():
                return self._prepare_decision_packet_inner(raw_input, target_amount)
        return self._prepare_decision_packet_inner(raw_input, target_amount)

    def _prepare_decision_packet_inner(
        self,
        raw_input: str,
        target_amount: float | None,
    ) -> dict:
        balance = self.ledger.get_balance()
        shortfall = max(0.0, (target_amount or 0.0) - balance)
        directive = Directive(raw_input=raw_input)

        revenue_proposal = RevenueProposal(
            summary="CRO proposes funding the directive through the fastest realistic revenue path.",
            target_amount=target_amount,
            shortfall=shortfall,
            proposed_paths=[
                "Sell a focused service offer",
                "Create a small digital product",
                "Use existing project assets for near-term revenue",
            ],
        )
        self.audit.record(
            "decision_chain.cro_proposed",
            "CRO prepared revenue proposal",
            detail=revenue_proposal.model_dump(mode="json"),
            agent_role="cro",
            directive_id=directive.id,
        )

        financial_evaluation = FinancialEvaluation(
            current_balance=balance,
            target_amount=target_amount,
            shortfall=shortfall,
            viable=shortfall <= max(balance * 10, 500.0),
            rationale=(
                "Shortfall appears within a range that can be evaluated through a revenue plan."
                if shortfall
                else "Current balance can cover the target amount."
            ),
        )
        self.audit.record(
            "decision_chain.cfo_evaluated",
            "CFO evaluated financial viability",
            detail=financial_evaluation.model_dump(mode="json"),
            agent_role="cfo",
            directive_id=directive.id,
        )

        synthesis = DecisionSynthesis(
            consensus="Proceed only after user approval; do not execute yet.",
            risks=[
                "Revenue assumptions may be wrong.",
                "Timeline may exceed user expectation.",
                "Execution may compete with active projects.",
            ],
            recommendation="Prepare execution plan and request user approval.",
        )
        self.audit.record(
            "decision_chain.cos_synthesized",
            "CoS synthesized decision packet",
            detail=synthesis.model_dump(mode="json"),
            agent_role="cos",
            directive_id=directive.id,
        )

        ceo_approval = CEOApprovalPacket(
            approved_direction="Prepare for execution after approval.",
            rationale="The plan preserves mission integrity while keeping the user as final decision maker.",
        )
        self.audit.record(
            "decision_chain.ceo_approved_direction",
            "CEO approved direction for user review",
            detail=ceo_approval.model_dump(mode="json"),
            agent_role="ceo",
            directive_id=directive.id,
        )

        execution_plan = COOExecutionPlan(
            steps=[
                "Confirm user approval",
                "Create execution project",
                "Assign researcher/analyst/writer/builder/procurement tasks as needed",
                "Review outputs through responsible C-level agents",
            ],
            assigned_agents=["coo", "researcher", "analyst", "writer", "builder", "procurement"],
        )
        self.audit.record(
            "decision_chain.coo_planned",
            "COO prepared execution plan",
            detail=execution_plan.model_dump(mode="json"),
            agent_role="coo",
            directive_id=directive.id,
        )

        packet = DecisionChainPacket(
            raw_input=raw_input,
            revenue_proposal=revenue_proposal,
            financial_evaluation=financial_evaluation,
            synthesis=synthesis,
            ceo_approval=ceo_approval,
            execution_plan=execution_plan,
        )
        request = self.approvals.create(ApprovalRequest(
            action_type="decision_chain_execution",
            summary=f"Approve decision packet: {raw_input[:120]}",
            payload={"packet": packet.model_dump(mode="json")},
            directive_id=directive.id,
            requested_by="AutonomyGate",
            severity="medium",
        ))
        packet.approval_id = request.id
        self.audit.record(
            "decision_chain.autonomy_requested",
            "AutonomyGate requested user approval for decision packet",
            detail={"approval_id": request.id, "packet_id": packet.id},
            directive_id=directive.id,
        )
        return packet.model_dump(mode="json")

    def execute_decision_packet(
        self,
        approval_id: str,
        executor: str = "master",
    ) -> dict:
        """Execute a user-approved decision-chain packet under governance.

        Pipeline:
            approved packet → materialize Project + Tasks
                          → COO dispatch via ProjectRunner
                          → C-level review (cro/cfo/cos/ceo)
                          → delivery approval request (no auto-delivery)
        """
        if current_run_id() is None:
            with run_scope():
                return self._execute_decision_packet_inner(approval_id, executor)
        return self._execute_decision_packet_inner(approval_id, executor)

    def _execute_decision_packet_inner(
        self,
        approval_id: str,
        executor: str,
    ) -> dict:
        from kompany.core.runner import ProjectRunner

        request = self.approvals.get(approval_id)
        if request is None:
            raise ValueError(f"Approval '{approval_id}' not found")
        if request.action_type != "decision_chain_execution":
            raise ValueError(
                f"Approval '{approval_id}' is not a decision_chain_execution "
                f"(got '{request.action_type}')"
            )
        if request.status != ApprovalStatus.APPROVED:
            raise ValueError(
                f"Approval '{approval_id}' is not approved "
                f"(status='{request.status.value}')"
            )

        packet_data = (request.payload or {}).get("packet")
        if not packet_data:
            raise ValueError(
                f"Approval '{approval_id}' payload has no packet"
            )
        packet = DecisionChainPacket.model_validate(packet_data)

        project = self._materialize_packet_project(packet, request)
        self.audit.record(
            "governed_execution.materialized",
            "Materialized project from approved decision packet",
            detail={
                "approval_id": approval_id,
                "project_id": project.id,
                "packet_id": packet.id,
                "task_count": len(packet.execution_plan.steps),
            },
            project_id=project.id,
        )

        self.agent_status.set(
            "coo",
            "dispatching",
            project.name,
            project_id=project.id,
            project_type=project.type.value,
        )
        self.audit.record(
            "governed_execution.dispatched",
            "COO dispatched project execution",
            detail={"project_id": project.id},
            agent_role="coo",
            project_id=project.id,
        )
        try:
            run_result = ProjectRunner(self).run(project.id)
        finally:
            self.agent_status.set("coo", "idle")

        reviews = self._c_level_review(project, run_result)

        any_revision = any(r.verdict != "approved" for r in reviews)
        report_status = (
            "needs_revision" if any_revision else "awaiting_delivery_approval"
        )

        delivery_request = self.approvals.create(ApprovalRequest(
            action_type="delivery_approval",
            summary=f"Approve delivery for: {packet.raw_input[:120]}",
            payload={
                "project_id": project.id,
                "packet_id": packet.id,
                "tasks_completed": run_result.tasks_completed,
                "tasks_failed": run_result.tasks_failed,
                "reviews": [r.model_dump() for r in reviews],
                "outputs": run_result.outputs,
                "report_status": report_status,
            },
            project_id=project.id,
            requested_by="AutonomyGate",
            severity="high",
        ))
        self.audit.record(
            "governed_execution.delivery_requested",
            "Requested delivery approval",
            detail={
                "approval_id": delivery_request.id,
                "project_id": project.id,
                "report_status": report_status,
            },
            project_id=project.id,
        )

        report = ExecutionReport(
            project_id=project.id,
            approval_id=approval_id,
            packet_id=packet.id,
            status=report_status,
            tasks_completed=run_result.tasks_completed,
            tasks_failed=run_result.tasks_failed,
            outputs=run_result.outputs,
            reviews=reviews,
            delivery_approval_id=delivery_request.id,
            total_ai_cost=run_result.total_ai_cost,
        )
        return report.model_dump(mode="json")

    def release_delivery(
        self,
        approval_id: str,
        released_by: str = "master",
    ) -> dict:
        """Release outputs to the user once a delivery_approval is approved.

        Idempotent: a second call returns ``status="already_delivered"`` with
        no project mutation and no duplicate audit entry.
        """
        if current_run_id() is None:
            with run_scope():
                return self._release_delivery_inner(approval_id, released_by)
        return self._release_delivery_inner(approval_id, released_by)

    def _release_delivery_inner(
        self,
        approval_id: str,
        released_by: str,
    ) -> dict:
        request = self.approvals.get(approval_id)
        if request is None or request.action_type != "delivery_approval":
            self.audit.record(
                "governed_execution.release_blocked",
                "Release blocked: missing or wrong-type approval",
                detail={
                    "approval_id": approval_id,
                    "action_type": request.action_type if request else None,
                },
            )
            raise ValueError(
                f"Approval '{approval_id}' is not a delivery_approval"
            )

        payload = request.payload or {}
        reviews = [
            CLevelReview.model_validate(r)
            for r in payload.get("reviews", [])
        ]

        if request.status == ApprovalStatus.REJECTED:
            self.audit.record(
                "governed_execution.release_blocked",
                "Release blocked: delivery rejected",
                detail={"approval_id": approval_id},
                project_id=payload.get("project_id"),
            )
            package = DeliveryPackage(
                approval_id=approval_id,
                project_id=payload.get("project_id"),
                packet_id=payload.get("packet_id"),
                status="needs_revision",
                tasks_completed=payload.get("tasks_completed", 0),
                tasks_failed=payload.get("tasks_failed", 0),
                outputs=payload.get("outputs", []),
                reviews=reviews,
                notes=request.resolution_reason or "Delivery rejected; revise outputs.",
            )
            return package.model_dump(mode="json")

        if request.status != ApprovalStatus.APPROVED:
            self.audit.record(
                "governed_execution.release_blocked",
                "Release blocked: approval not approved",
                detail={
                    "approval_id": approval_id,
                    "status": request.status.value,
                },
                project_id=payload.get("project_id"),
            )
            raise ValueError(
                f"Approval '{approval_id}' is not approved "
                f"(status='{request.status.value}')"
            )

        if payload.get("released_at"):
            package = DeliveryPackage(
                approval_id=approval_id,
                project_id=payload.get("project_id"),
                packet_id=payload.get("packet_id"),
                status="already_delivered",
                tasks_completed=payload.get("tasks_completed", 0),
                tasks_failed=payload.get("tasks_failed", 0),
                outputs=payload.get("outputs", []),
                reviews=reviews,
                released_at=payload.get("released_at"),
                released_by=payload.get("released_by"),
            )
            return package.model_dump(mode="json")

        project_id = payload.get("project_id")
        if project_id:
            self.projects.update_status(project_id, ProjectStatus.COMPLETED)

        from datetime import datetime as _dt, UTC as _UTC
        released_at = _dt.now(_UTC).isoformat()
        self.approvals.update_payload(
            approval_id,
            {"released_at": released_at, "released_by": released_by},
        )
        self.audit.record(
            "governed_execution.released",
            "Released delivery package to user",
            detail={
                "approval_id": approval_id,
                "project_id": project_id,
                "released_by": released_by,
            },
            project_id=project_id,
        )

        package = DeliveryPackage(
            approval_id=approval_id,
            project_id=project_id,
            packet_id=payload.get("packet_id"),
            status="delivered",
            tasks_completed=payload.get("tasks_completed", 0),
            tasks_failed=payload.get("tasks_failed", 0),
            outputs=payload.get("outputs", []),
            reviews=reviews,
            released_at=released_at,
            released_by=released_by,
        )

        if project_id:
            try:
                self.run_retrospective(project_id)
            except Exception:
                # Retrospective is best-effort; never block delivery release.
                pass

        return package.model_dump(mode="json")

