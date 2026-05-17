"""KompanyEngine — the single entry point for all interfaces."""

from __future__ import annotations

import time
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
from kompany.notifications import build_notifier
from kompany.remote import RemoteCommandRequest, RemoteCommandResult, parse_remote_text
from kompany.state.agent_status import AgentStatusStore
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.checkpoints import CheckpointStore
from kompany.state.credentials import ALLOWED_CREDENTIALS, CredentialVaultStore
from kompany.state.vault_keys import resolve_vault_key
from kompany.state.database import Database
from kompany.state.journal import Journal
from kompany.state.ledger import Ledger
from kompany.state.models import (
    CLevelReview,
    CompanySnapshot,
    Decision,
    ApprovalRequest,
    ApprovalStatus,
    CEOApprovalPacket,
    COOExecutionPlan,
    DecisionChainPacket,
    DecisionSynthesis,
    DeliveryPackage,
    ExecutionReport,
    HeartbeatReport,
    NotificationEvent,
    ObservabilitySnapshot,
    RPGCharacter,
    RPGOfficeRoom,
    Reflection,
    Retrospective,
    FinancialEvaluation,
    LedgerCategory,
    Project,
    ProjectStatus,
    ProjectType,
    RevenueProposal,
)
from kompany.state.backup import BackupManager
from kompany.state.projects import Projects
from kompany.state.memory import AgentMemory
from kompany.state.runtime import RuntimeStateStore
from kompany.state.remote_replay import RemoteReplayStore
from kompany.state.tool_authorization import ToolAuthorizationStore


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
        self.audit = AuditLog(self.db)
        self.approvals = ApprovalRequests(self.db)
        self.agent_status = AgentStatusStore(self.db)
        self.checkpoints = CheckpointStore(self.db)
        self.runtime = RuntimeStateStore(self.db)
        self.remote_replay = RemoteReplayStore(self.db)
        self.credentials = CredentialVaultStore(self.db, self.settings.vault_key)
        self._apply_vault_credentials()
        self.tool_authorization = ToolAuthorizationStore(self.db)
        self.backups = BackupManager(self.settings.data_dir)
        self.cost_tracker = CostTracker(self.ledger)
        self.autonomy = AutonomyGate()

        self.llm = LLMClient(
            settings=self.settings,
            cost_tracker=self.cost_tracker,
            provider_error_handler=self._handle_provider_error,
        )
        self.registry = AgentRegistry(
            self.llm, self.settings, self.ledger, self.projects
        )

    def _resolve_vault_key(self) -> None:
        vault_key, source = resolve_vault_key(
            self.settings.vault_key,
            keychain_service=getattr(self.settings, "vault_keychain_service", "kompany"),
            keychain_account=getattr(self.settings, "vault_keychain_account", "vault-master-key"),
        )
        self.settings.vault_key = vault_key
        self.audit.record(
            "credential_vault.key_resolved",
            "Credential vault key resolved",
            detail={"source": source},
        )

    def _apply_vault_credentials(self) -> None:
        if not self.settings.vault_key:
            return
        for name in sorted(ALLOWED_CREDENTIALS):
            if getattr(self.settings, name, ""):
                continue
            value = self.credentials.get(name)
            if value:
                setattr(self.settings, name, value)

    def get_company_state(self) -> dict:
        """Get current company state for agent context."""
        return {
            "name": self.settings.company_name,
            "goal": self.settings.company_goal,
            "stage": self.settings.company_stage,
            "time_horizon": self.settings.company_time_horizon,
            "exclusions": self.settings.company_exclusions,
            "balance": self.ledger.get_balance(),
            "active_projects": self.projects.count_active(),
        }

    def _handle_provider_error(self, event: dict) -> None:
        if event.get("reason") != "quota_exhausted":
            return
        self.audit.record(
            "runtime.quota_exhausted",
            "LLM provider quota exhausted; suspending engine",
            detail=event,
            agent_role=event.get("agent_name"),
            directive_id=event.get("directive_id"),
        )
        self.suspend("quota_exhausted")

    def initialize_company(
        self,
        name: str,
        capital: float,
        goal: str = "",
        time_horizon: str = "",
        exclusions: str = "",
    ) -> None:
        """Initialize a new Kompany with starting capital."""
        self.settings.company_name = name
        self.settings.company_goal = goal
        self.settings.company_stage = "solo"
        self.settings.company_time_horizon = time_horizon
        self.settings.company_exclusions = exclusions
        # Record initial capital
        if capital > 0:
            self.ledger.record(
                amount=capital,
                description=f"Initial capital for {name}",
                category=LedgerCategory.INCOME,
                approved_by="master",
            )

    def execute_project(self, project_id: str) -> dict:
        """Execute a revenue project's tasks autonomously."""
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

        self.agent_status.set("coo", "dispatching", project.name)
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

    def run_retrospective(self, project_id: str) -> dict:
        """Deterministic CoS retrospective: persist one reflection per agent.

        Idempotent: if a retrospective already exists for the project,
        returns ``status="already_recorded"`` without writing or auditing.
        """
        project = self.projects.get(project_id)
        if project is None:
            self.audit.record(
                "learning.retrospective_skipped",
                "Retrospective skipped: project not found",
                detail={"project_id": project_id},
            )
            return Retrospective(
                project_id=project_id,
                status="skipped_no_project",
            ).model_dump(mode="json")

        existing_rows = self.db.execute(
            """SELECT agent_role, content FROM agent_memories
               WHERE category = 'reflection' AND context = ?""",
            (f"project:{project_id}",),
        ).fetchall()
        if existing_rows:
            reflections = [
                Reflection(agent_role=r["agent_role"], content=r["content"])
                for r in existing_rows
            ]
            self.audit.record(
                "learning.retrospective_skipped",
                "Retrospective already recorded for project",
                detail={"project_id": project_id},
                project_id=project_id,
            )
            return Retrospective(
                project_id=project_id,
                status="already_recorded",
                summary=project.name,
                reflections=reflections,
            ).model_dump(mode="json")

        tasks = self.projects.list_tasks(project_id)
        completed = sum(1 for t in tasks if t.status.value == "completed")
        failed = sum(1 for t in tasks if t.status.value == "failed")

        agents = list(dict.fromkeys(project.assigned_agents)) or ["coo"]
        reflections: list[Reflection] = []
        for role in agents:
            agent_tasks = [t for t in tasks if t.assigned_agent == role]
            agent_failed = sum(1 for t in agent_tasks if t.status.value == "failed")
            content = (
                f"Project '{project.name}' completed with "
                f"{len(agent_tasks)} task(s) assigned to {role}, "
                f"{agent_failed} failed; {completed} completed and {failed} "
                f"failed across the project."
            )
            self.memory.remember(
                agent_role=role,
                content=content,
                category="reflection",
                knowledge_type="experiential",
                context=f"project:{project_id}",
            )
            reflections.append(Reflection(agent_role=role, content=content))

        self.audit.record(
            "learning.retrospective_completed",
            "CoS retrospective recorded",
            detail={
                "project_id": project_id,
                "tasks_completed": completed,
                "tasks_failed": failed,
                "agent_roles": agents,
            },
            project_id=project_id,
        )

        return Retrospective(
            project_id=project_id,
            status="recorded",
            summary=project.name,
            tasks_completed=completed,
            tasks_failed=failed,
            reflections=reflections,
        ).model_dump(mode="json")

    def list_memories(
        self,
        agent_role: str,
        limit: int = 20,
        include_stale: bool = False,
        knowledge_type: str | None = None,
        category: str | None = None,
    ) -> list[dict]:
        """List memories for an agent, with stale/knowledge_type filters."""
        return self.memory.recall(
            agent_role=agent_role,
            limit=limit,
            category=category,
            include_stale=include_stale,
            knowledge_type=knowledge_type,
        )

    def get_runtime_state(self) -> dict:
        """Return the current persisted runtime state."""
        return self.runtime.get()

    def heartbeat_once(
        self,
        dispatch: bool = False,
        adapter: str = "dry-run",
    ) -> dict:
        """Inspect runtime state and emit notification-ready events."""
        runtime = self.get_runtime_state()
        approvals = self.list_approvals()
        active_projects = self.projects.list_active()
        notifications: list[NotificationEvent] = []

        if runtime["state"] == "suspended":
            notifications.append(NotificationEvent(
                kind="runtime_suspended",
                severity="warning",
                summary=f"Kompany runtime is suspended: {runtime['reason'] or 'unknown'}",
                payload=runtime,
            ))
        if approvals:
            notifications.append(NotificationEvent(
                kind="pending_approvals",
                severity="action_required",
                summary=f"{len(approvals)} approval request(s) awaiting user decision.",
                payload={"approval_ids": [a["id"] for a in approvals]},
            ))
        if active_projects:
            notifications.append(NotificationEvent(
                kind="active_projects",
                severity="info",
                summary=f"{len(active_projects)} active project(s) in progress.",
                payload={"project_ids": [p.id for p in active_projects]},
            ))

        report = HeartbeatReport(
            runtime=runtime,
            pending_approvals=len(approvals),
            active_projects=len(active_projects),
            notifications=notifications,
        )
        payload = report.model_dump(mode="json")
        self.audit.record(
            "heartbeat.tick",
            "Heartbeat checked runtime, approvals, and projects",
            detail={
                "runtime_state": runtime["state"],
                "pending_approvals": len(approvals),
                "active_projects": len(active_projects),
                "notifications": len(notifications),
            },
        )
        for event in payload["notifications"]:
            self.audit.record(
                "notification.emitted",
                event["summary"],
                detail=event,
            )
        if dispatch:
            payload["deliveries"] = self.dispatch_notifications(
                payload["notifications"],
                adapter=adapter,
            )
        return payload

    def dispatch_notifications(
        self,
        events: list[dict],
        adapter: str = "dry-run",
    ) -> list[dict]:
        """Dispatch notification events through a configured adapter."""
        notifier = build_notifier(self.settings, adapter=adapter)
        deliveries = []
        for event in events:
            delivery = notifier.send(event).model_dump(mode="json")
            audit_detail = {k: v for k, v in delivery.items() if k != "error"}
            if delivery.get("error"):
                audit_detail["error"] = delivery["error"]
            self.audit.record(
                "notification.dispatched",
                f"Notification dispatch {delivery['status']}: {event['summary']}",
                detail=audit_detail,
            )
            deliveries.append(delivery)
        return deliveries

    def handle_remote_command(self, request: RemoteCommandRequest | dict) -> dict:
        """Authenticate and execute a bounded inbound remote command."""
        if isinstance(request, dict):
            request = RemoteCommandRequest.model_validate(request)
        auth_error = self._remote_auth_error(request)
        command, args = parse_remote_text(request.text)
        if auth_error:
            result = RemoteCommandResult(
                source=request.source,
                status="denied",
                command=command,
                message=auth_error,
            ).model_dump(mode="json")
            self.audit.record(
                "remote_command.denied",
                f"Remote command denied: {request.source}:{command}",
                detail={"source": request.source, "command": command, "reason": auth_error},
            )
            return result

        replay_key = self._remote_replay_key(request)
        if replay_key:
            self.cleanup_remote_replays()
            replayed = self.remote_replay.get(request.source, replay_key)
            if replayed is not None:
                self.audit.record(
                    "remote_command.replayed",
                    f"Remote command replayed: {request.source}:{command}",
                    detail={"source": request.source, "command": command},
                )
                return {**replayed, "replayed": True}

        result = self._execute_remote_command(request.source, command, args)
        result["replayed"] = False
        if replay_key:
            self.remote_replay.store(request.source, replay_key, command, result)
        self.audit.record(
            f"remote_command.{result['status']}",
            f"Remote command {result['status']}: {request.source}:{command}",
            detail={"source": request.source, "command": command, "status": result["status"]},
        )
        return result

    def cleanup_remote_replays(self, ttl_seconds: int | None = None) -> dict:
        ttl = ttl_seconds
        if ttl is None:
            ttl = self.settings.remote_replay_ttl_seconds
        result = self.remote_replay.cleanup(ttl)
        self.audit.record(
            "remote_command.replay_cleanup",
            "Remote command replay cache cleaned up",
            detail={
                "deleted": result["deleted"],
                "remaining": result["remaining"],
                "ttl_seconds": result["ttl_seconds"],
                "cutoff": result["cutoff"],
            },
        )
        return result

    def _remote_replay_key(self, request: RemoteCommandRequest) -> str:
        payload = request.payload or {}
        if payload.get("nonce") is not None:
            return str(payload["nonce"])
        if payload.get("request_id") is not None:
            return str(payload["request_id"])
        if request.source == "telegram" and payload.get("update_id") is not None:
            return str(payload["update_id"])
        return ""

    def _remote_auth_error(self, request: RemoteCommandRequest) -> str:
        if request.source == "telegram":
            allowed = {
                chat_id.strip()
                for chat_id in self.settings.telegram_allowed_chat_ids.split(",")
                if chat_id.strip()
            }
            if not allowed:
                return "telegram remote control is not configured"
            if request.chat_id not in allowed:
                return "telegram chat is not authorized"
            return ""
        if request.source == "mobile":
            expected = self.settings.mobile_remote_token
            if not expected:
                return "mobile remote control is not configured"
            if request.bearer_token != expected:
                return "mobile bearer token is invalid"
            return ""
        return "remote source is not supported"

    def _execute_remote_command(
        self,
        source: str,
        command: str,
        args: list[str],
    ) -> dict:
        if command in {"help", ""}:
            return RemoteCommandResult(
                source=source,
                status="executed",
                command="help",
                message="Supported commands: status, approvals, approve <id>, reject <id> [reason], heartbeat, help",
                result={
                    "commands": ["status", "approvals", "approve", "reject", "heartbeat", "help"],
                },
            ).model_dump(mode="json")
        if command == "status":
            return RemoteCommandResult(
                source=source,
                status="executed",
                command=command,
                message="Kompany status snapshot",
                result=self.observability_snapshot(),
            ).model_dump(mode="json")
        if command == "approvals":
            approvals = self.list_approvals()
            return RemoteCommandResult(
                source=source,
                status="executed",
                command=command,
                message=f"{len(approvals)} pending approval(s)",
                result={"approvals": approvals},
            ).model_dump(mode="json")
        if command == "heartbeat":
            return RemoteCommandResult(
                source=source,
                status="executed",
                command=command,
                message="Heartbeat report",
                result=self.heartbeat_once(),
            ).model_dump(mode="json")
        if command == "approve" and args:
            approval = self.approve_request(args[0])
            return RemoteCommandResult(
                source=source,
                status="executed" if approval else "unknown_command",
                command=command,
                message="Approval updated" if approval else f"Approval '{args[0]}' not found",
                result=approval,
            ).model_dump(mode="json")
        if command == "reject" and args:
            reason = " ".join(args[1:]) if len(args) > 1 else "remote rejection"
            approval = self.reject_request(args[0], reason=reason)
            return RemoteCommandResult(
                source=source,
                status="executed" if approval else "unknown_command",
                command=command,
                message="Approval rejected" if approval else f"Approval '{args[0]}' not found",
                result=approval,
            ).model_dump(mode="json")
        return RemoteCommandResult(
            source=source,
            status="unknown_command",
            command=command,
            message="Unknown or incomplete remote command. Send 'help'.",
        ).model_dump(mode="json")

    def observability_snapshot(self) -> dict:
        """Return an LLM-free operational snapshot for dashboards and RPG views."""
        cfo = self.registry.get("cfo")
        finance = cfo.get_summary()
        runtime = self.get_runtime_state()
        approvals = self.list_approvals()
        active_projects = self.projects.list_active()
        all_agents = self._observability_agents()
        tool_policies = self.list_tool_policies()
        notifications = self.heartbeat_once()["notifications"]

        project_rows = []
        task_totals = {"pending": 0, "active": 0, "completed": 0, "failed": 0}
        for project in active_projects:
            tasks = self.projects.list_tasks(project.id)
            counts = {"pending": 0, "active": 0, "completed": 0, "failed": 0}
            for task in tasks:
                status = task.status.value if hasattr(task.status, "value") else task.status
                counts[status] = counts.get(status, 0) + 1
                task_totals[status] = task_totals.get(status, 0) + 1
            project_rows.append({
                "id": project.id,
                "name": project.name,
                "type": project.type.value,
                "status": project.status.value,
                "target_amount": project.target_amount,
                "funded_amount": project.funded_amount,
                "assigned_agents": project.assigned_agents,
                "tasks": counts,
            })

        blocked = []
        if runtime["state"] == "suspended":
            blocked.append({"kind": "runtime", "summary": runtime.get("reason") or "suspended"})
        for approval in approvals:
            blocked.append({
                "kind": "approval",
                "id": approval["id"],
                "summary": approval["summary"],
            })

        office = self._rpg_office(all_agents, project_rows, blocked)
        snapshot = ObservabilitySnapshot(
            company={
                "name": self.settings.company_name,
                "goal": self.settings.company_goal,
                "stage": self.settings.company_stage,
                "time_horizon": self.settings.company_time_horizon,
                "exclusions": self.settings.company_exclusions,
            },
            runtime=runtime,
            finance={
                "balance": finance["balance"],
                "total_income": finance["total_income"],
                "total_expenses": finance["total_expenses"],
                "total_ai_costs": abs(finance["total_ai_costs"]),
            },
            approvals={
                "pending": len(approvals),
                "items": approvals,
                "blockers": blocked,
            },
            projects={
                "active": len(active_projects),
                "items": project_rows,
                "task_totals": task_totals,
            },
            agents={
                "total": len(all_agents),
                "active": sum(1 for a in all_agents if a["status"] != "idle"),
                "items": all_agents,
            },
            tools={
                "policies": len(tool_policies),
                "allowed": sum(1 for p in tool_policies if p["allowed"]),
                "denied": sum(1 for p in tool_policies if not p["allowed"]),
            },
            notifications=notifications,
            office=office,
        ).model_dump(mode="json")
        self.audit.record(
            "observability.snapshot",
            "Generated observability snapshot",
            detail={
                "runtime_state": runtime["state"],
                "pending_approvals": len(approvals),
                "active_projects": len(active_projects),
                "active_agents": snapshot["agents"]["active"],
            },
        )
        return snapshot

    def _observability_agents(self) -> list[dict]:
        roles = [
            "ceo", "cfo", "cto", "cpo", "cro", "cmo", "coo", "cos",
            "ciso", "csa", "cv", "analyst", "builder", "procurement",
            "researcher", "writer",
        ]
        current = {row["agent_role"]: row for row in self.agent_status.list_all()}
        agents = []
        for role in roles:
            row = current.get(role, {})
            agents.append({
                "role": role,
                "status": row.get("status", "idle"),
                "current_task": row.get("current_task") or "",
                "updated_at": row.get("updated_at"),
            })
        return agents

    def _rpg_office(
        self,
        agents: list[dict],
        projects: list[dict],
        blockers: list[dict],
    ) -> dict:
        room_map = {
            "executive_suite": {
                "purpose": "Strategy, final direction, and governance.",
                "roles": {"ceo", "cos", "cfo"},
            },
            "growth_floor": {
                "purpose": "Revenue, product, marketing, and customer work.",
                "roles": {"cro", "cpo", "cmo", "cv"},
            },
            "operations_room": {
                "purpose": "Execution, delivery, and project coordination.",
                "roles": {"coo", "analyst", "writer", "researcher", "builder", "procurement"},
            },
            "security_lab": {
                "purpose": "Security, compliance, and tool authorization.",
                "roles": {"cto", "ciso", "csa"},
            },
        }
        rooms = []
        for name, spec in room_map.items():
            characters = [
                RPGCharacter(
                    role=agent["role"],
                    room=name,
                    status=agent["status"],
                    current_task=agent["current_task"],
                    updated_at=agent["updated_at"],
                )
                for agent in agents
                if agent["role"] in spec["roles"]
            ]
            rooms.append(RPGOfficeRoom(
                name=name,
                purpose=spec["purpose"],
                characters=characters,
            ).model_dump(mode="json"))
        return {
            "theme": "virtual_company_floor",
            "rooms": rooms,
            "active_projects": [p["name"] for p in projects],
            "blockers": blockers,
        }

    def list_credentials(self) -> list[dict]:
        return [entry.model_dump(mode="json") for entry in self.credentials.list()]

    def set_credential(self, name: str, value: str) -> dict:
        entry = self.credentials.set(name, value)
        if not getattr(self.settings, name, ""):
            setattr(self.settings, name, value)
        self.audit.record(
            "credential_vault.updated",
            f"Credential updated: {name}",
            detail={"name": name},
        )
        return entry.model_dump(mode="json")

    def delete_credential(self, name: str) -> dict:
        result = self.credentials.delete(name)
        self.audit.record(
            "credential_vault.deleted",
            f"Credential deleted: {name}",
            detail={"name": name, "deleted": result["deleted"]},
        )
        return result

    def rotate_credential_key(self, new_vault_key: str) -> dict:
        result = self.credentials.rotate_key(new_vault_key)
        self.settings.vault_key = new_vault_key
        self.credentials = CredentialVaultStore(self.db, self.settings.vault_key)
        try:
            from kompany.state.vault_keys import set_vault_key_in_keychain

            set_vault_key_in_keychain(
                new_vault_key,
                service=getattr(self.settings, "vault_keychain_service", "kompany"),
                account=getattr(self.settings, "vault_keychain_account", "vault-master-key"),
            )
        except ImportError:
            pass
        self.audit.record(
            "credential_vault.key_rotated",
            "Credential vault key rotated",
            detail={"rotated": result["rotated"], "names": result["names"]},
        )
        return result

    def list_tool_policies(self, agent_role: str | None = None) -> list[dict]:
        """List tool authorization policies."""
        return [
            policy.model_dump(mode="json")
            for policy in self.tool_authorization.list(agent_role=agent_role)
        ]

    def set_tool_policy(
        self,
        agent_role: str,
        tool_name: str,
        allowed: bool,
        reason: str = "",
        requires_approval: bool = False,
    ) -> dict:
        """Create or update a tool authorization policy."""
        policy = self.tool_authorization.set(
            agent_role=agent_role,
            tool_name=tool_name,
            allowed=allowed,
            reason=reason,
            requires_approval=requires_approval,
        )
        payload = policy.model_dump(mode="json")
        self.audit.record(
            "tool_authorization.policy_updated",
            f"Tool policy updated for {agent_role}:{tool_name}",
            detail=payload,
            agent_role=agent_role,
        )
        return payload

    def authorize_tool(
        self,
        agent_role: str,
        tool_name: str,
        purpose: str = "",
    ) -> dict:
        """Check whether an agent role may use a named tool."""
        from kompany.state.models import ToolAuthorizationResult

        policy = self.tool_authorization.get(agent_role, tool_name)
        allowed = bool(policy and policy.allowed)
        reason = (
            policy.reason
            if policy
            else "No policy exists for this agent role and tool."
        )
        status = "allowed" if allowed else "denied"
        if allowed and policy and policy.requires_approval:
            status = "approval_required"
        result = ToolAuthorizationResult(
            agent_role=agent_role,
            tool_name=tool_name,
            allowed=allowed,
            status=status,
            reason=reason,
        ).model_dump(mode="json")
        self.audit.record(
            f"tool_authorization.{status}",
            f"Tool authorization {status}: {agent_role} -> {tool_name}",
            detail={**result, "purpose": purpose},
            agent_role=agent_role,
        )
        return result

    def use_tool(
        self,
        agent_role: str,
        tool_name: str,
        purpose: str = "",
        arguments: dict | None = None,
        handler=None,
        approval_id: str | None = None,
    ) -> dict:
        """Authorize and optionally execute a tool through the engine gate."""
        from kompany.state.models import ApprovalRequest, ApprovalStatus, ToolAuthorizationResult

        auth = self.authorize_tool(agent_role, tool_name, purpose=purpose)
        if not auth["allowed"]:
            return auth
        if auth["status"] == "approval_required":
            approval = self._matching_tool_use_approval(
                approval_id,
                agent_role,
                tool_name,
                purpose,
            )
            if not approval or approval.status != ApprovalStatus.APPROVED:
                request = approval or self.approvals.create(ApprovalRequest(
                    action_type="tool_use",
                    summary=f"Approve tool use: {agent_role} -> {tool_name}",
                    payload={
                        "agent_role": agent_role,
                        "tool_name": tool_name,
                        "purpose": purpose,
                    },
                    requested_by="ToolAuthorizationGate",
                ))
                result = ToolAuthorizationResult(
                    agent_role=agent_role,
                    tool_name=tool_name,
                    allowed=True,
                    status="approval_required",
                    reason=auth["reason"],
                    approval_id=request.id,
                ).model_dump(mode="json")
                if approval is None:
                    self.audit.record(
                        "tool_authorization.approval_requested",
                        f"Tool use approval requested: {agent_role} -> {tool_name}",
                        detail={
                            "agent_role": agent_role,
                            "tool_name": tool_name,
                            "purpose": purpose,
                            "approval_id": request.id,
                        },
                        agent_role=agent_role,
                    )
                return result
        if handler is None:
            return ToolAuthorizationResult(
                agent_role=agent_role,
                tool_name=tool_name,
                allowed=True,
                status="allowed",
                reason=auth["reason"],
                approval_id=approval_id,
            ).model_dump(mode="json")
        try:
            output = handler(arguments or {})
        except Exception as exc:
            failure = ToolAuthorizationResult(
                agent_role=agent_role,
                tool_name=tool_name,
                allowed=True,
                status="failed",
                reason=f"{type(exc).__name__}: tool execution failed",
            ).model_dump(mode="json")
            self.audit.record(
                "tool_authorization.execution_failed",
                f"Tool execution failed: {agent_role} -> {tool_name}",
                detail=failure,
                agent_role=agent_role,
            )
            return failure
        executed = ToolAuthorizationResult(
            agent_role=agent_role,
            tool_name=tool_name,
            allowed=True,
            status="executed",
            reason=auth["reason"],
            approval_id=approval_id,
            result=output if isinstance(output, dict) else {"value": output},
        ).model_dump(mode="json")
        self.audit.record(
            "tool_authorization.executed",
            f"Tool executed: {agent_role} -> {tool_name}",
            detail={k: v for k, v in executed.items() if k != "result"},
            agent_role=agent_role,
        )
        return executed

    def _matching_tool_use_approval(
        self,
        approval_id: str | None,
        agent_role: str,
        tool_name: str,
        purpose: str,
    ):
        if not approval_id:
            return None
        request = self.approvals.get(approval_id)
        if request is None or request.action_type != "tool_use":
            return None
        payload = request.payload or {}
        if (
            payload.get("agent_role") == agent_role
            and payload.get("tool_name") == tool_name
            and payload.get("purpose", "") == purpose
        ):
            return request
        return None

    def suspend(self, reason: str = "manual") -> dict:
        """Suspend the engine. Idempotent: re-suspending is a no-op."""
        current = self.runtime.get()
        if current["state"] == "suspended":
            return {**current, "status": "already_suspended"}
        new_state = self.runtime.set("suspended", reason=reason)
        self.audit.record(
            "runtime.suspended",
            f"Engine suspended ({reason})",
            detail={"reason": reason},
        )
        return {**new_state, "status": "suspended"}

    def resume(self) -> dict:
        """Resume the engine. Idempotent: re-resuming is a no-op."""
        current = self.runtime.get()
        if current["state"] == "running":
            return {**current, "status": "already_running"}
        new_state = self.runtime.set("running", reason=None)
        self.audit.record(
            "runtime.resumed",
            "Engine resumed",
            detail={"previous_reason": current["reason"]},
        )
        return {**new_state, "status": "resumed"}

    def create_backup(self, label: str = "manual") -> dict:
        """Create a labeled SQLite snapshot of the live database."""
        meta = self.backups.create_backup(label=label, kind="manual")
        self.audit.record(
            "backup.created",
            f"Backup created: {meta['id']}",
            detail={
                "id": meta["id"],
                "label": label,
                "size_bytes": meta["size_bytes"],
            },
        )
        return meta

    def list_backups(self) -> list[dict]:
        """List all SQLite snapshots, newest first."""
        return self.backups.list_backups()

    def restore_backup(self, backup_id: str) -> dict:
        """Restore a snapshot, automatically creating a pre-restore backup.

        Re-binds dependent stores so subsequent queries see the restored state
        without restarting the process. Raises ``FileNotFoundError`` if the
        backup does not exist.
        """
        meta = self.backups.get(backup_id)
        if meta is None:
            raise FileNotFoundError(f"Backup '{backup_id}' not found")

        # 1. Auto pre-restore snapshot (recorded in current live audit).
        auto_meta = self.backups.create_backup(
            label=f"pre-restore-{backup_id}", kind="auto"
        )
        self.audit.record(
            "backup.auto_created",
            f"Auto-pre-restore backup: {auto_meta['id']}",
            detail={"id": auto_meta["id"], "for_restore": backup_id},
        )

        # 2. Close live DB and swap file.
        self.db.close()
        result = self.backups.restore_backup(backup_id)

        # 3. Rebind all dependent stores to the new connection.
        self.db = Database(self.settings.data_dir)
        self.ledger = Ledger(self.db)
        self.journal = Journal(self.db)
        self.projects = Projects(self.db)
        self.memory = AgentMemory(self.db)
        self.audit = AuditLog(self.db)
        self.approvals = ApprovalRequests(self.db)
        self.agent_status = AgentStatusStore(self.db)
        self.checkpoints = CheckpointStore(self.db)
        self.runtime = RuntimeStateStore(self.db)
        self.remote_replay = RemoteReplayStore(self.db)
        self.credentials = CredentialVaultStore(self.db, self.settings.vault_key)
        self._apply_vault_credentials()
        self.tool_authorization = ToolAuthorizationStore(self.db)
        self.cost_tracker = CostTracker(self.ledger)

        # 4. Audit restore in the (now restored) live DB.
        self.audit.record(
            "backup.restored",
            f"Backup restored: {backup_id}",
            detail={
                "id": backup_id,
                "auto_pre_restore_id": auto_meta["id"],
                "restored_from": result["restored_from"],
            },
        )
        return {
            **meta,
            "restored_at": result["restored_at"],
            "auto_pre_restore_id": auto_meta["id"],
        }

    def _materialize_packet_project(
        self,
        packet: DecisionChainPacket,
        request: ApprovalRequest,
    ) -> Project:
        """Create a Project and its Tasks from an approved decision packet."""
        from kompany.state.models import Task, TaskStatus

        project_type = (
            ProjectType.REVENUE
            if packet.revenue_proposal.shortfall > 0
            else ProjectType.OPERATIONAL
        )
        plan_agents = packet.execution_plan.assigned_agents or ["coo"]
        assigned = ["coo"] + [a for a in plan_agents if a != "coo"]

        project = Project(
            name=f"Execute: {packet.raw_input[:50]}",
            type=project_type,
            target_amount=packet.revenue_proposal.target_amount,
            triggers_directive_id=request.directive_id,
            plan={"packet": packet.model_dump(mode="json")},
            assigned_agents=assigned,
        )
        self.projects.create(project)

        steps = packet.execution_plan.steps or ["Execute approved packet"]
        for index, step in enumerate(steps):
            agent = plan_agents[index % len(plan_agents)] if plan_agents else "coo"
            task = Task(
                project_id=project.id,
                title=step,
                assigned_agent=agent,
                status=TaskStatus.PENDING,
            )
            self.projects.create_task(task)
        return project

    def _c_level_review(self, project: Project, run_result) -> list[CLevelReview]:
        """Deterministic C-level review of executed packet outputs."""
        failed_count = run_result.tasks_failed
        completed_count = run_result.tasks_completed
        verdict = "approved" if failed_count == 0 else "needs_revision"

        notes_ok = (
            f"{completed_count} task(s) completed without failures."
        )
        notes_revision = (
            f"{failed_count} task(s) failed; "
            f"review failed task outputs before delivery."
        )
        base_note = notes_ok if verdict == "approved" else notes_revision

        roles = ["cro", "cfo", "cos", "ceo"]
        reviews: list[CLevelReview] = []
        for role in roles:
            review = CLevelReview(owner=role, verdict=verdict, notes=base_note)
            reviews.append(review)
            self.audit.record(
                "governed_execution.reviewed",
                f"{role.upper()} reviewed packet execution",
                detail=review.model_dump(),
                agent_role=role,
                project_id=project.id,
            )
        return reviews

    def process_override(self, text: str) -> dict:
        """Create a risk briefing and approval request for a user override."""
        directive = Directive(raw_input=text)
        directive.status = DirectiveStatus.AWAITING_APPROVAL
        briefing = {
            "summary": f"Override requested: {text}",
            "risks": [
                "May invalidate the current plan or assumptions.",
                "May affect budget, schedule, or active project priorities.",
                "May require revisiting prior team recommendations.",
            ],
            "required_confirmation": "Approve only after accepting these risks.",
            "will_execute_immediately": False,
        }
        request = self.approvals.create(ApprovalRequest(
            action_type="override",
            summary=f"Approve override: {text[:120]}",
            payload={"override": text, "briefing": briefing},
            directive_id=directive.id,
            requested_by="KompanyEngine",
        ))
        self.audit.record(
            "override.risk_briefing_created",
            "Created override risk briefing",
            detail={"approval_id": request.id},
            directive_id=directive.id,
        )
        return {
            "status": "awaiting_approval",
            "approval_id": request.id,
            "briefing": briefing,
        }

    def process_directive(self, raw_input: str) -> DirectiveResult:
        """Main entry point. Takes natural language, returns result."""
        directive = Directive(raw_input=raw_input)

        rt = self.runtime.get()
        if rt["state"] == "suspended":
            self.audit.record(
                "directive.suspended_skip",
                "Skipped directive: runtime suspended",
                detail={"reason": rt["reason"], "input_length": len(raw_input)},
                directive_id=directive.id,
            )
            return DirectiveResult(
                directive=directive,
                status="suspended",
                message=(
                    f"Engine is suspended ({rt['reason'] or 'manual'}). "
                    "Call resume() to continue."
                ),
                agents_used=[],
                total_ai_cost=0.0,
            )

        state = self.get_company_state()
        self.audit.record(
            "directive.received",
            "Received user directive",
            detail={"input_length": len(raw_input)},
            directive_id=directive.id,
        )

        start_time = time.time()
        try:
            self.agent_status.set("ceo", "thinking", "classifying directive")
            ceo = self.registry.get("ceo", company_state=state)
            classification = ceo.classify(raw_input, directive_id=directive.id)
            self.audit.record(
                "directive.classified",
                "CEO classified directive",
                detail=classification.model_dump(),
                agent_role="ceo",
                directive_id=directive.id,
            )

            directive.directive_type = DirectiveType(classification.directive_type)
            directive.assigned_squad = classification.primary_squad
            directive.assigned_agents = classification.agents_needed
            directive.requires_approval = classification.approval_tier
            directive.budget_required = classification.estimated_cost_eur
            directive.budget_available = self.ledger.get_balance()

            handler = {
                DirectiveType.ACQUISITION: self._handle_acquisition,
                DirectiveType.STRATEGIC: self._handle_strategic,
                DirectiveType.OPERATIONAL: self._handle_operational,
                DirectiveType.INFORMATIONAL: self._handle_informational,
            }.get(directive.directive_type, self._handle_operational)
            self.audit.record(
                "directive.routed",
                "Routed directive to handler",
                detail={"directive_type": directive.directive_type.value},
                directive_id=directive.id,
            )

            result = handler(directive, classification, ceo)

            self.journal.log(Decision(
                directive_id=directive.id,
                directive_type=directive.directive_type.value if directive.directive_type else "unknown",
                raw_input=directive.raw_input,
                classification=classification.model_dump() if classification else {},
                result={"status": result.status, "message": result.message[:500]},
                agents_involved=result.agents_used,
                total_ai_cost=result.total_ai_cost,
                duration_seconds=time.time() - start_time,
            ))
            self.audit.record(
                "journal.recorded",
                "Recorded directive decision journal entry",
                detail={"status": result.status},
                directive_id=directive.id,
            )
            self.audit.record(
                "directive.completed",
                "Completed directive processing",
                detail={"status": result.status},
                directive_id=directive.id,
            )
            return result
        except Exception as exc:
            self.audit.record(
                "directive.failed",
                "Directive processing failed",
                detail={"error": str(exc)},
                directive_id=directive.id,
            )
            raise
        finally:
            self.agent_status.set("ceo", "idle")

    def _record_autonomy_result(
        self,
        directive: Directive,
        can_auto_proceed: bool,
        estimated_cost: float | None,
    ) -> str | None:
        event_type = (
            "autonomy.auto_approved"
            if can_auto_proceed
            else "autonomy.approval_required"
        )
        approval_id = None
        if not can_auto_proceed:
            request = self.approvals.create(ApprovalRequest(
                action_type="directive_execution",
                summary=f"Approve directive: {directive.raw_input[:120]}",
                payload={
                    "directive_type": directive.directive_type.value if directive.directive_type else None,
                    "approval_tier": directive.requires_approval,
                    "estimated_cost": estimated_cost,
                },
                directive_id=directive.id,
                requested_by="AutonomyGate",
            ))
            approval_id = request.id
        self.audit.record(
            event_type,
            "AutonomyGate evaluated directive",
            detail={
                "approval_tier": directive.requires_approval,
                "estimated_cost": estimated_cost,
                "approval_id": approval_id,
            },
            directive_id=directive.id,
        )
        return approval_id

    def list_approvals(self) -> list[dict]:
        """List pending approval requests."""
        return [request.model_dump(mode="json") for request in self.approvals.list_pending()]

    def approve_request(self, request_id: str, approved_by: str = "master") -> dict | None:
        """Approve a pending request."""
        request = self.approvals.approve(request_id, approved_by=approved_by)
        if request:
            self.audit.record(
                "approval.approved",
                "Approved pending request",
                detail={"approval_id": request.id, "action_type": request.action_type},
                directive_id=request.directive_id,
                project_id=request.project_id,
            )
            return request.model_dump(mode="json")
        return None

    def reject_request(
        self,
        request_id: str,
        rejected_by: str = "master",
        reason: str | None = None,
    ) -> dict | None:
        """Reject a pending request."""
        request = self.approvals.reject(
            request_id,
            rejected_by=rejected_by,
            reason=reason,
        )
        if request:
            self.audit.record(
                "approval.rejected",
                "Rejected pending request",
                detail={
                    "approval_id": request.id,
                    "action_type": request.action_type,
                    "reason": reason,
                },
                directive_id=request.directive_id,
                project_id=request.project_id,
            )
            return request.model_dump(mode="json")
        return None

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
