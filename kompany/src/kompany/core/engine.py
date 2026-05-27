"""KompanyEngine — the single entry point for all interfaces."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from kompany.agents.registry import AgentRegistry
from kompany.config.settings import KompanySettings
from kompany.core.autonomy import AutonomyGate
from kompany.core.directive import (
    Directive,
    DirectiveResult,
    DirectiveStatus,
    DirectiveType,
)
from kompany.core.event_hub import get_event_hub
from kompany.core.run_context import current_run_id, run_scope
from kompany.core.watchdog import LLMUnavailable, Watchdog
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
from kompany.state.debates import Debates
from kompany.state.episodes import Episodes
from kompany.state.health_events import HealthEvents
from kompany.state.projects import Projects
from kompany.state.memory import AgentMemory
from kompany.state.runtime import RuntimeStateStore
from kompany.state.remote_replay import RemoteReplayStore
from kompany.state.glossary import (
    CompanyGlossary,
    GlossaryEntry,
    GlossaryService,
    load_from_config as load_glossary_from_config,
)
from kompany.state.targets import (
    CompanyTargets,
    TargetsBundle,
    compose_summary as compose_targets_summary,
    get_bundle as get_targets_bundle,
    get_state as get_targets_state,
    get_targets as get_company_targets,
    set_review_thread_id as set_targets_review_thread_id,
    set_targets as set_company_targets,
)
from kompany.state.ui_preferences import (
    UIPreferences,
    get_preferences as get_ui_preferences,
    set_preferences as set_ui_preferences,
)
from kompany.state.templates import (
    Templates,
    TemplateAlreadyApplied,
    TemplateNotFound,
)
from kompany.state.tool_authorization import ToolAuthorizationStore


from kompany.core.directive_proposal import DirectiveProposalMixin
from kompany.core.target_review import TargetReviewMixin


class KompanyEngine(TargetReviewMixin, DirectiveProposalMixin):
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
        self.debates = Debates(self.db)
        self.episodes = Episodes(self.db)
        self.health_events = HealthEvents(self.db)
        self.approvals = ApprovalRequests(self.db)
        self.agent_status = AgentStatusStore(self.db)
        self.checkpoints = CheckpointStore(self.db)
        self.runtime = RuntimeStateStore(self.db)
        self.remote_replay = RemoteReplayStore(self.db)
        # Resolve the vault key BEFORE constructing the store so the
        # credential decrypt path works on first call. Without this,
        # Tauri sidecar (which doesn't get KOMPANY_VAULT_KEY in env)
        # boots with vault_key="" → _apply_vault_credentials silently
        # no-ops → custom_api_key + custom_base_url stay empty on every
        # subsequent engine instance → LLMClient routes via model-name
        # prefix (gpt-5.5 → openai.com) and the custom-provider key
        # 401s. Keychain lookup carries the key across sidecar restarts.
        if not self.settings.vault_key:
            try:
                vault_key, _source = resolve_vault_key(
                    self.settings.vault_key,
                    keychain_service=getattr(
                        self.settings, "vault_keychain_service", "kompany"
                    ),
                    keychain_account=getattr(
                        self.settings, "vault_keychain_account", "vault-master-key"
                    ),
                    data_dir=self.settings.data_dir,
                )
                self.settings.vault_key = vault_key
            except Exception:  # noqa: BLE001 — first-boot resolution miss is fine
                pass
        self.credentials = CredentialVaultStore(self.db, self.settings.vault_key)
        self._apply_vault_credentials()
        self.tool_authorization = ToolAuthorizationStore(self.db)
        self.templates = Templates(
            db=self.db,
            ledger=self.ledger,
            projects=self.projects,
            audit=self.audit,
        )
        self.glossary = GlossaryService(self.db)
        self.backups = BackupManager(self.settings.data_dir)
        # STREAM layer of the cost visibility discipline: every LLM
        # cost recording fans out a ``llm.spend`` SSE event so the web
        # UI's dashboard chip / live cost meter stays in sync without
        # polling. See ``05-19-cost-visibility-discipline``.
        self.cost_tracker = CostTracker(self.ledger, event_hub=get_event_hub())
        self.autonomy = AutonomyGate()

        # Resilience watchdog: silent-run + stranded-task supervisor.
        # Defaults live in code; ``company_config`` overrides take effect
        # at engine construction time.
        self.watchdog = Watchdog(
            health_events=self.health_events,
            projects=self.projects,
            audit=self.audit,
            scan_interval_seconds=self._get_int_config(
                "stranded_scan_interval_seconds", default=60
            ),
            stale_threshold_seconds=self._get_int_config(
                "task_stale_threshold_seconds", default=600
            ),
            approvals=self.approvals,
            # Wire the runway provider so each scanner tick can compare
            # projected burn against the agreed targets. Wrapped in a
            # try/except so a transient ledger error never breaks the
            # tick — see ``Watchdog._scan_runway`` for the contract.
            runway_provider=self._runway_snapshot,
        )

        self.llm = LLMClient(
            settings=self.settings,
            cost_tracker=self.cost_tracker,
            provider_error_handler=self._handle_provider_error,
            audit_log=self.audit,
            watchdog=self.watchdog,
            silent_timeout_seconds=self._get_int_config(
                "llm_silent_timeout_seconds", default=90
            ),
        )
        self.registry = AgentRegistry(
            self.llm, self.settings, self.ledger, self.projects
        )

        # Revision-handler registry. Keyed by ``ApprovalRequest.action_type``;
        # each handler receives ``(original_approval, hint_text)`` and must
        # return a freshly persisted ``ApprovalRequest`` whose
        # ``predecessor_id`` points back at the original. Action types
        # without a registered handler fall through to
        # ``_default_revision_handler`` (see below) so the player flow never
        # dead-ends. Registered here so callers can swap in
        # caller-specific LLM-driven re-plan paths in a later task.
        self._revision_handlers: dict[
            str,
            Callable[[ApprovalRequest, str], ApprovalRequest],
        ] = {}
        # The target_feasibility action_type uses a dedicated revision
        # handler so a founder counter-proposal carries the parsed numbers
        # forward into the successor approval (not just a hint string).
        self.register_revision_handler(
            "target_feasibility",
            self._target_feasibility_revision_handler,
        )
        # Glossary review revisions: founder can accept a subset of the
        # proposed corrections by leaving them in the payload and dropping
        # the rest in the ``revision_hint``. See
        # ``_glossary_review_revision_handler`` for the full contract.
        self.register_revision_handler(
            "glossary_review",
            self._glossary_review_revision_handler,
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
        if self.settings.vault_key:
            for name in sorted(ALLOWED_CREDENTIALS):
                if getattr(self.settings, name, ""):
                    continue
                value = self.credentials.get(name)
                if value:
                    setattr(self.settings, name, value)
        # Custom-provider tier override: onboarding writes the discovered
        # model id into company_config so every engine boot re-applies
        # the override. Without this, settings fall through to the
        # Anthropic-tier defaults (claude-sonnet-4-*) and LLMClient
        # routes the agent debate through the Anthropic SDK — which
        # auth-fails against a custom-provider API key.
        try:
            row = self.db.execute(
                "SELECT value FROM company_config WHERE key = ?",
                ("custom_model_picked",),
            ).fetchone()
        except Exception:  # noqa: BLE001 — pre-init absence is fine
            row = None
        if row and row["value"]:
            picked = row["value"]
            self.settings.model_apex = picked
            self.settings.model_primary = picked
            self.settings.model_economy = picked

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

    def run_retrospective(self, project_id: str) -> dict:
        """Deterministic CoS retrospective: persist one reflection per agent.

        Idempotent: if a retrospective already exists for the project,
        returns ``status="already_recorded"`` without writing or auditing.
        """
        if current_run_id() is None:
            with run_scope():
                return self._run_retrospective_inner(project_id)
        return self._run_retrospective_inner(project_id)

    def _run_retrospective_inner(self, project_id: str) -> dict:
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

        # Glossary drift scan (glossary-and-drift-detection task 05-19).
        # Runs *after* reflections land in agent_memories but *before*
        # episode materialization so the resulting health event + drift
        # rows are already on disk when ``Episodes.materialize`` reads
        # them. Wrapped in try/except: a drift-scan bug must never block
        # the canonical retrospective output.
        try:
            self._run_glossary_drift_scan(
                project_id=project_id,
                reflections=reflections,
            )
        except Exception as exc:  # pragma: no cover - defensive
            self.audit.record(
                "glossary.drift_scan_failed",
                "Glossary drift scan failed",
                detail={"project_id": project_id, "error": str(exc)},
                project_id=project_id,
            )

        # Materialize the structured episode record + enforce retention.
        # Wrapped in try/except so that a materialization bug never blocks
        # a retrospective from being written (reflections are the user-visible
        # output; episodes are the durable analysis substrate).
        try:
            episode_row = self.episodes.record_or_update(project_id)
            self.audit.record(
                "learning.episode_recorded",
                "Materialized project episode",
                detail={
                    "project_id": project_id,
                    "retention_tier": episode_row["retention_tier"],
                },
                project_id=project_id,
            )
            max_full = self._get_int_config(
                "episode_retention_full_count", default=50
            )
            trimmed = self.episodes.trim_to_retention_window(max_full)
            for entry in trimmed:
                self.audit.record(
                    "learning.episode_trimmed",
                    "Episode demoted to summary retention",
                    detail=entry,
                    project_id=entry["project_id"],
                )
        except Exception as exc:  # pragma: no cover - defensive
            self.audit.record(
                "learning.episode_failed",
                "Episode materialization failed",
                detail={"project_id": project_id, "error": str(exc)},
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

    # ------------------------------------------------------------------
    # Health events (resilience watchdog)
    # ------------------------------------------------------------------

    def list_health_events(
        self,
        status: str | None = None,
        project_id: str | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List health events, newest-first, optionally filtered."""
        return self.health_events.list(
            status=status,
            project_id=project_id,
            kind=kind,
            limit=limit,
        )

    def get_health_event(self, event_id: str) -> dict[str, Any] | None:
        """Fetch a single health event by id."""
        return self.health_events.get(event_id)

    def resolve_health_event(
        self,
        event_id: str,
        action: str,
        snooze_minutes: int | None = None,
        resolved_by: str = "player",
    ) -> dict[str, Any] | None:
        """Apply a player action (``continue`` / ``snooze`` / ``dismiss``)."""
        if action == "snooze" and snooze_minutes is None:
            snooze_minutes = self._get_int_config(
                "health_default_snooze_minutes", default=30
            )
        return self.watchdog.resolve(
            event_id=event_id,
            action=action,
            resolved_by=resolved_by,
            snooze_minutes=snooze_minutes,
        )

    def scan_stranded_tasks(self) -> list[dict[str, Any]]:
        """One-shot stranded-task sweep. Returns the events written."""
        if current_run_id() is None:
            with run_scope():
                return self.watchdog.scan_once()
        return self.watchdog.scan_once()

    def mark_task_stranded(
        self,
        task_id: str,
        project_id: str | None = None,
        reason: str = "llm_unavailable",
    ) -> dict[str, Any]:
        """Flip a task to ``stranded_in_progress`` + write health event.

        Called by the engine when an ``LLMUnavailable`` is caught after
        the watchdog's retry budget is exhausted.
        """
        if current_run_id() is None:
            with run_scope():
                return self._mark_task_stranded_inner(task_id, project_id, reason)
        return self._mark_task_stranded_inner(task_id, project_id, reason)

    def _mark_task_stranded_inner(
        self,
        task_id: str,
        project_id: str | None,
        reason: str,
    ) -> dict[str, Any]:
        try:
            self.projects.update_task_status_raw(
                task_id=task_id,
                status="stranded_in_progress",
            )
        except Exception:
            pass
        return self.watchdog.record_stranded_in_progress(
            task_id=task_id,
            project_id=project_id,
            detail={"reason": reason},
        )

    async def start(self) -> None:
        """Start engine background workers (watchdog scanner)."""
        self.watchdog.start()

    async def stop(self) -> None:
        """Stop engine background workers."""
        await self.watchdog.stop()

    # ------------------------------------------------------------------
    # Company templates (ready-to-play scenarios)
    # ------------------------------------------------------------------

    def list_templates(self) -> list[dict[str, Any]]:
        """Return all available company templates as dicts."""
        return [tpl.model_dump() for tpl in self.templates.list_templates()]

    def show_template(self, template_id: str) -> dict[str, Any]:
        """Return one template's manifest + rendered mission body."""
        try:
            tpl, mission = self.templates.show_with_mission(template_id)
        except TemplateNotFound as exc:
            raise ValueError(str(exc)) from exc
        payload = tpl.model_dump()
        payload["mission"] = mission
        return payload

    def apply_template(
        self,
        template_id: str,
        force: bool = False,
        override_budget: float | None = None,
        override_directive: str | None = None,
        override_revenue_target: float | None = None,
        override_customer_target: int | None = None,
        override_deadline: str | None = None,
    ) -> dict[str, Any]:
        """Apply a ready-to-play template to the current company.

        Wraps :meth:`Templates.apply` in an audit-friendly run scope and
        normalizes the structured errors into ``ValueError`` so every
        surface (CLI, REST, MCP, SDK) handles them uniformly.
        """
        if current_run_id() is None:
            with run_scope():
                return self._apply_template_inner(
                    template_id,
                    force=force,
                    override_budget=override_budget,
                    override_directive=override_directive,
                    override_revenue_target=override_revenue_target,
                    override_customer_target=override_customer_target,
                    override_deadline=override_deadline,
                )
        return self._apply_template_inner(
            template_id,
            force=force,
            override_budget=override_budget,
            override_directive=override_directive,
            override_revenue_target=override_revenue_target,
            override_customer_target=override_customer_target,
            override_deadline=override_deadline,
        )

    def _apply_template_inner(
        self,
        template_id: str,
        *,
        force: bool,
        override_budget: float | None,
        override_directive: str | None,
        override_revenue_target: float | None = None,
        override_customer_target: int | None = None,
        override_deadline: str | None = None,
    ) -> dict[str, Any]:
        try:
            result = self.templates.apply(
                template_id,
                force=force,
                override_budget=override_budget,
                override_directive=override_directive,
                override_revenue_target=override_revenue_target,
                override_customer_target=override_customer_target,
                override_deadline=override_deadline,
            )
        except TemplateNotFound as exc:
            raise ValueError(str(exc)) from exc
        except TemplateAlreadyApplied as exc:
            raise ValueError(str(exc)) from exc
        return result.model_dump()

    def _get_int_config(self, key: str, default: int) -> int:
        """Read an integer config value from ``company_config``.

        Falls back to ``default`` if the row is missing or unparseable.
        """
        row = self.db.execute(
            "SELECT value FROM company_config WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return default

    def list_episodes(
        self,
        retention_tier: str | None = None,
    ) -> list[dict[str, Any]]:
        """List materialized project episodes (no payload)."""
        rows = self.episodes.list(retention_tier=retention_tier)
        # Strip the heavy payload column from the list view; callers who
        # want the full payload should call ``get_episode``.
        return [
            {k: v for k, v in row.items() if k != "payload_json"}
            for row in rows
        ]

    def get_episode(self, project_id: str) -> dict[str, Any] | None:
        """Fetch one episode row including its ``payload_json``."""
        return self.episodes.get(project_id)

    def rebuild_episode(self, project_id: str) -> dict[str, Any]:
        """Force re-materialization of one project's episode payload.

        Use this after manually mutating source-table rows (e.g. backfilling
        a missing audit event) to refresh the cached payload. The operation
        is idempotent and re-applies retention trimming.
        """
        if current_run_id() is None:
            with run_scope():
                return self._rebuild_episode_inner(project_id)
        return self._rebuild_episode_inner(project_id)

    def _rebuild_episode_inner(self, project_id: str) -> dict[str, Any]:
        row = self.episodes.record_or_update(project_id)
        self.audit.record(
            "learning.episode_recorded",
            "Episode rebuilt on demand",
            detail={
                "project_id": project_id,
                "retention_tier": row["retention_tier"],
                "trigger": "rebuild",
            },
            project_id=project_id,
        )
        max_full = self._get_int_config("episode_retention_full_count", default=50)
        trimmed = self.episodes.trim_to_retention_window(max_full)
        for entry in trimmed:
            self.audit.record(
                "learning.episode_trimmed",
                "Episode demoted to summary retention",
                detail=entry,
                project_id=entry["project_id"],
            )
        return row

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

    # ------------------------------------------------------------------
    # Cross-episode distillation (P1 self-learning)
    # ------------------------------------------------------------------

    def distill(
        self,
        since: Any = None,
        dry_run: bool = False,
        episode_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run cross-episode distillation as CoS.

        Pulls the recent ``project_episodes`` rows, asks CoS to identify
        durable cross-project patterns, and UPSERTs each pattern into
        ``agent_memories`` (``category='experiential'``) keyed by
        ``(agent_role, pattern_key)``.

        Parameters
        ----------
        since:
            A ``timedelta`` controlling the time window. ``None`` uses
            :data:`kompany.agents.cos_distillation.DEFAULT_SINCE` (30 days).
            Ignored when ``episode_ids`` is provided.
        dry_run:
            If ``True``, the LLM call still happens (so the operator can
            inspect what would be written) but no rows are written to
            ``agent_memories`` and no audit event is recorded.
        episode_ids:
            Explicit subset of project ids to distil. Bypasses the
            ``since`` window and the 50-episode cap.
        """
        if current_run_id() is None:
            with run_scope():
                return self._distill_inner(
                    since=since,
                    dry_run=dry_run,
                    episode_ids=episode_ids,
                )
        return self._distill_inner(
            since=since,
            dry_run=dry_run,
            episode_ids=episode_ids,
        )

    def _distill_inner(
        self,
        *,
        since: Any,
        dry_run: bool,
        episode_ids: list[str] | None,
    ) -> dict[str, Any]:
        from datetime import timedelta

        from kompany.agents.cos_distillation import (
            DEFAULT_SINCE,
            MAX_EPISODES_PER_RUN,
            build_episode_summaries,
            filter_inferred_only_patterns,
            filter_patterns,
            select_episode_rows,
        )

        window = since if since is not None else DEFAULT_SINCE
        # Strings/numbers from REST or CLI callers get coerced to timedelta
        # here so the selection helper sees a consistent type.
        if isinstance(window, (int, float)):
            window = timedelta(seconds=float(window))
        if not isinstance(window, timedelta) and window is not None:
            raise ValueError(
                f"since must be a timedelta or numeric seconds, got {type(window).__name__}"
            )

        # ``list`` returns rows in newest-first order with full payload
        # column. We need the payloads to summarize so ``list_episodes``
        # (which strips payload_json) isn't an option here.
        all_rows = self.episodes.list()
        selected = select_episode_rows(
            all_rows,
            episode_ids=episode_ids,
            since=window if not episode_ids else None,
        )

        # Hard cap unless the operator explicitly selected episodes.
        if episode_ids is None and len(selected) > MAX_EPISODES_PER_RUN:
            raise ValueError(
                f"too many episodes in window ({len(selected)} > "
                f"{MAX_EPISODES_PER_RUN}); use --episodes to select a subset"
            )

        run_id = current_run_id()

        # No-input fast path: nothing to learn, nothing to bill for. We
        # still emit an audit event so operators can see the run happened.
        if not selected:
            result = {
                "status": "no_episodes",
                "episodes_in": 0,
                "patterns_out": 0,
                "patterns": [],
                "ai_cost": 0.0,
                "run_id": run_id,
                "dry_run": dry_run,
            }
            self.audit.record(
                "learning.distillation_run",
                "Distillation run produced no patterns (empty episode window)",
                detail={
                    "episodes_in": 0,
                    "patterns_out": 0,
                    "ai_cost": 0.0,
                    "dry_run": dry_run,
                    "run_id": run_id,
                },
            )
            return result

        summaries, parse_failures = build_episode_summaries(selected)
        if not summaries:
            # Every selected row had a malformed payload. Surface this as
            # ``no_episodes`` rather than calling the LLM with nothing.
            self.audit.record(
                "learning.distillation_failed",
                "All selected episodes had malformed payloads",
                detail={
                    "episodes_in": len(selected),
                    "parse_failures": parse_failures,
                    "dry_run": dry_run,
                    "run_id": run_id,
                },
            )
            return {
                "status": "no_parseable_episodes",
                "episodes_in": len(selected),
                "patterns_out": 0,
                "patterns": [],
                "ai_cost": 0.0,
                "run_id": run_id,
                "dry_run": dry_run,
                "parse_failures": parse_failures,
            }

        # Run the LLM call. The CoS agent + the LLMClient wrapper handle
        # run_id propagation, audit events, ledger cost accounting,
        # silent-run detection, and retry on transient failure.
        cos_agent = self.registry.get("cos")
        try:
            # Inject the agreed-target summary so distillation can
            # pattern-match around the company's revenue/customer/
            # deadline shape (mission-targets task 05-19).
            resp = cos_agent.distill(
                summaries,
                targets_summary=self._compose_targets_summary(),
                glossary_summary=self._compose_glossary_summary(),
            )
        except Exception as exc:
            self.audit.record(
                "learning.distillation_failed",
                "CoS LLM call failed during distillation",
                detail={
                    "episodes_in": len(summaries),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                    "dry_run": dry_run,
                    "run_id": run_id,
                },
            )
            raise

        parsed = resp.parsed
        if parsed is None:
            # ``call_structured`` either parses or raises; defensive only.
            self.audit.record(
                "learning.distillation_failed",
                "CoS distillation returned no parsed output",
                detail={
                    "episodes_in": len(summaries),
                    "dry_run": dry_run,
                    "run_id": run_id,
                },
            )
            raise RuntimeError("CoS distillation returned no parsed output")

        patterns, warnings = filter_patterns(parsed)

        # Evidence-trace guard (task 05-19): drop inferred-only patterns
        # (no ``evidence_episode_ids``) before they pollute
        # ``agent_memories``. Each rejection fires its own audit event so
        # the founder can see "team learned 5 things, 3 were rejected".
        patterns, claim_rejections = filter_inferred_only_patterns(patterns)
        for rejection in claim_rejections:
            self.audit.record(
                event_type="distillation.claim_rejected_inferred_only",
                action="Distillation rejected an inferred-only claim",
                detail={
                    "pattern_key": rejection["pattern_key"],
                    "target_agent_role": rejection["target_agent_role"],
                    "claim_text": rejection["claim_text"],
                    "run_id": run_id,
                    "dry_run": dry_run,
                },
            )

        # Write phase. ``dry_run`` short-circuits all DB writes; the audit
        # event still fires so operators can see who triggered the dry run.
        written: list[dict[str, Any]] = []
        if not dry_run:
            for pattern in patterns:
                action_meta = {
                    "pattern_key": pattern.pattern_key,
                    "confidence": pattern.confidence,
                    "evidence_episode_ids": list(pattern.evidence_episode_ids),
                }
                # Distillation usually emits ``experiential`` patterns; the
                # glossary-and-drift-detection task (05-19) allows CoS to
                # tag a pattern ``glossary_proposal`` when it spots a
                # repeated drift worth canonicalising. The founder then
                # approves the new term via the inbox before it shapes
                # any future agent prompt.
                memory_category = pattern.category or "experiential"
                knowledge_type = (
                    "glossary_proposal"
                    if memory_category == "glossary_proposal"
                    else "experiential"
                )
                upsert = self.memory.upsert_by_pattern_key(
                    agent_role=pattern.target_agent_role,
                    pattern_key=pattern.pattern_key,
                    content=pattern.pattern_summary,
                    metadata=action_meta,
                    category=memory_category,
                    knowledge_type=knowledge_type,
                    run_id=run_id,
                )
                written.append({
                    "agent_role": pattern.target_agent_role,
                    "pattern_key": pattern.pattern_key,
                    "memory_id": upsert["id"],
                    "action": upsert["action"],
                    "confidence": pattern.confidence,
                })

        result_patterns = [
            {
                "target_agent_role": p.target_agent_role,
                "pattern_key": p.pattern_key,
                "pattern_summary": p.pattern_summary,
                "confidence": p.confidence,
                "evidence_episode_ids": list(p.evidence_episode_ids),
            }
            for p in patterns
        ]

        result = {
            "status": "completed",
            "episodes_in": len(summaries),
            "patterns_out": len(patterns),
            "patterns": result_patterns,
            "ai_cost": float(resp.cost_usd),
            "run_id": run_id,
            "dry_run": dry_run,
            "warnings": warnings,
            "writes": written,
            "parse_failures": parse_failures,
            "claims_rejected_inferred_only": claim_rejections,
        }

        self.audit.record(
            "learning.distillation_run",
            "CoS cross-episode distillation completed",
            detail={
                "episodes_in": len(summaries),
                "patterns_out": len(patterns),
                "ai_cost": float(resp.cost_usd),
                "dry_run": dry_run,
                "writes": [
                    {
                        "agent_role": w["agent_role"],
                        "pattern_key": w["pattern_key"],
                        "action": w["action"],
                    }
                    for w in written
                ],
                "warnings": warnings,
                "run_id": run_id,
            },
        )
        return result

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
                    severity="critical",
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
        self.debates = Debates(self.db)
        self.episodes = Episodes(self.db)
        self.health_events = HealthEvents(self.db)
        self.approvals = ApprovalRequests(self.db)
        self.agent_status = AgentStatusStore(self.db)
        self.checkpoints = CheckpointStore(self.db)
        self.runtime = RuntimeStateStore(self.db)
        self.remote_replay = RemoteReplayStore(self.db)
        # Resolve the vault key BEFORE constructing the store so the
        # credential decrypt path works on first call. Without this,
        # Tauri sidecar (which doesn't get KOMPANY_VAULT_KEY in env)
        # boots with vault_key="" → _apply_vault_credentials silently
        # no-ops → custom_api_key + custom_base_url stay empty on every
        # subsequent engine instance → LLMClient routes via model-name
        # prefix (gpt-5.5 → openai.com) and the custom-provider key
        # 401s. Keychain lookup carries the key across sidecar restarts.
        if not self.settings.vault_key:
            try:
                vault_key, _source = resolve_vault_key(
                    self.settings.vault_key,
                    keychain_service=getattr(
                        self.settings, "vault_keychain_service", "kompany"
                    ),
                    keychain_account=getattr(
                        self.settings, "vault_keychain_account", "vault-master-key"
                    ),
                    data_dir=self.settings.data_dir,
                )
                self.settings.vault_key = vault_key
            except Exception:  # noqa: BLE001 — first-boot resolution miss is fine
                pass
        self.credentials = CredentialVaultStore(self.db, self.settings.vault_key)
        self._apply_vault_credentials()
        self.tool_authorization = ToolAuthorizationStore(self.db)
        # Re-wire after backup restore: same hub instance, fresh ledger.
        self.cost_tracker = CostTracker(self.ledger, event_hub=get_event_hub())

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
        if current_run_id() is None:
            with run_scope():
                return self._process_override_inner(text)
        return self._process_override_inner(text)

    def _process_override_inner(self, text: str) -> dict:
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
            severity="high",
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
        """Main entry point. Takes natural language, returns result.

        Opens a fresh ``run_scope`` so every state write made during this
        directive (audit_log, decisions, ledger, memories, approvals)
        carries the same ``run_id``. A nested call (e.g. CEO derives a
        child directive) records the outer ``run_id`` as ``parent_run_id``
        automatically — see :func:`kompany.core.run_context.run_scope`.
        """
        with run_scope():
            return self._process_directive_inner(raw_input)

    def _process_directive_inner(self, raw_input: str) -> DirectiveResult:
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
            # Inject the agreed-target summary so CEO classify weighs the
            # ask against the company's explicit revenue/customer/deadline
            # commitments (mission-targets task 05-19). Falls back to an
            # innocuous default when no targets are set.
            classification = ceo.classify(
                raw_input,
                directive_id=directive.id,
                targets_summary=self._compose_targets_summary(),
                glossary_summary=self._compose_glossary_summary(),
            )
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

            decision_result_payload: dict[str, Any] = {
                "status": result.status,
                "message": result.message[:500],
            }
            if result.project_id:
                decision_result_payload["project_id"] = result.project_id
            if result.debate_id:
                decision_result_payload["debate_id"] = result.debate_id
            if result.approval_id:
                decision_result_payload["approval_id"] = result.approval_id
            self.journal.log(Decision(
                directive_id=directive.id,
                directive_type=directive.directive_type.value if directive.directive_type else "unknown",
                raw_input=directive.raw_input,
                classification=classification.model_dump() if classification else {},
                result=decision_result_payload,
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
                severity="medium",
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

    def trace_run(self, run_id: str) -> dict:
        """Return all state writes tagged with ``run_id``, time-ordered.

        Pulls from audit_log, decisions, ledger, agent_memories, tasks,
        and approval_requests. Each record carries a ``kind`` so callers
        can format mixed streams without re-introspecting columns.
        """
        events: list[dict] = []

        for row in self.db.execute(
            """SELECT timestamp, event_type, agent_role, action, detail,
                      directive_id, project_id, run_id
               FROM audit_log WHERE run_id = ? ORDER BY id""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "audit",
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "agent_role": row["agent_role"],
                "action": row["action"],
                "detail": row["detail"],
                "directive_id": row["directive_id"],
                "project_id": row["project_id"],
            })

        for row in self.db.execute(
            """SELECT timestamp, id, directive_id, directive_type,
                      result, agents_involved, total_ai_cost,
                      duration_seconds
               FROM decisions WHERE run_id = ? ORDER BY timestamp""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "decision",
                "timestamp": row["timestamp"],
                "id": row["id"],
                "directive_id": row["directive_id"],
                "directive_type": row["directive_type"],
                "result": row["result"],
                "agents_involved": row["agents_involved"],
                "total_ai_cost": row["total_ai_cost"],
                "duration_seconds": row["duration_seconds"],
            })

        for row in self.db.execute(
            """SELECT timestamp, amount, balance_after, description,
                      category, directive_id, project_id
               FROM ledger WHERE run_id = ? ORDER BY id""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "ledger",
                "timestamp": row["timestamp"],
                "amount": row["amount"],
                "balance_after": row["balance_after"],
                "description": row["description"],
                "category": row["category"],
                "directive_id": row["directive_id"],
                "project_id": row["project_id"],
            })

        for row in self.db.execute(
            """SELECT created_at, agent_role, category, knowledge_type,
                      content, context, directive_id
               FROM agent_memories WHERE run_id = ? ORDER BY id""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "memory",
                "timestamp": row["created_at"],
                "agent_role": row["agent_role"],
                "category": row["category"],
                "knowledge_type": row["knowledge_type"],
                "content": row["content"],
                "context": row["context"],
                "directive_id": row["directive_id"],
            })

        for row in self.db.execute(
            """SELECT created_at, id, project_id, title, status,
                      assigned_agent
               FROM tasks WHERE run_id = ? ORDER BY created_at""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "task",
                "timestamp": row["created_at"],
                "id": row["id"],
                "project_id": row["project_id"],
                "title": row["title"],
                "status": row["status"],
                "assigned_agent": row["assigned_agent"],
            })

        for row in self.db.execute(
            """SELECT created_at, id, status, action_type, summary,
                      directive_id, project_id, requested_by, resolved_by
               FROM approval_requests WHERE run_id = ? ORDER BY created_at""",
            (run_id,),
        ).fetchall():
            events.append({
                "kind": "approval",
                "timestamp": row["created_at"],
                "id": row["id"],
                "status": row["status"],
                "action_type": row["action_type"],
                "summary": row["summary"],
                "directive_id": row["directive_id"],
                "project_id": row["project_id"],
                "requested_by": row["requested_by"],
                "resolved_by": row["resolved_by"],
            })

        events.sort(key=lambda e: (e.get("timestamp") or "", e.get("kind", "")))
        return {
            "run_id": run_id,
            "event_count": len(events),
            "events": events,
        }

    def list_approvals(self) -> list[dict]:
        """List pending approval requests."""
        return [request.model_dump(mode="json") for request in self.approvals.list_pending()]

    def inbox(
        self,
        statuses: tuple[str, ...] = ("pending", "snoozed"),
    ) -> list[dict]:
        """Return the player's RPG inbox: actionable approvals + counts.

        ``pending`` and ``snoozed`` rows are surfaced by default — terminal
        states (``approved``/``rejected``/``revision_requested``/``cancelled``)
        are read via ``approval show <id>`` or the episode retrospective.

        Each row carries ``comment_count`` so the inbox renderer can show
        "3 comments" without a second per-row query.
        """
        rows: list[dict] = []
        for status in statuses:
            for request in self.approvals.list_by_status(status=status):
                payload = request.model_dump(mode="json")
                payload["comment_count"] = len(
                    self.approvals.list_comments(request.id)
                )
                rows.append(payload)
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows

    def get_approval(self, request_id: str) -> dict | None:
        """Return one approval + its full thread (predecessors + successors)
        + comments. Used by ``approval show`` across all four surfaces."""
        request = self.approvals.get(request_id)
        if request is None:
            return None
        thread = [r.model_dump(mode="json") for r in self.approvals.list_thread(request_id)]
        comments = [
            c.model_dump(mode="json")
            for c in self.approvals.list_comments(request_id)
        ]
        result = request.model_dump(mode="json")
        result["thread"] = thread
        result["comments"] = comments
        return result

    def approve_request(
        self,
        request_id: str,
        approved_by: str = "master",
        comment_body: str | None = None,
    ) -> dict | None:
        """Approve a pending request."""
        request = self.approvals.approve(
            request_id,
            approved_by=approved_by,
            comment_body=comment_body,
        )
        if request:
            self.audit.record(
                "approval.approved",
                "Approved pending request",
                detail={"approval_id": request.id, "action_type": request.action_type},
                directive_id=request.directive_id,
                project_id=request.project_id,
            )
            # Action-type-specific post-resolve hook. Keep this list short
            # and inline so new action_types are easy to spot.
            if request.action_type == "target_feasibility":
                self._finalize_target_feasibility(request, outcome="approved")
            if request.action_type == "glossary_review":
                self._finalize_glossary_review(request, outcome="approved")
            return request.model_dump(mode="json")
        return None

    def reject_request(
        self,
        request_id: str,
        rejected_by: str = "master",
        reason: str | None = None,
        comment_body: str | None = None,
    ) -> dict | None:
        """Reject a pending request."""
        request = self.approvals.reject(
            request_id,
            rejected_by=rejected_by,
            reason=reason,
            comment_body=comment_body,
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
            if request.action_type == "target_feasibility":
                self._finalize_target_feasibility(request, outcome="rejected")
            if request.action_type == "glossary_review":
                self._finalize_glossary_review(request, outcome="rejected")
            return request.model_dump(mode="json")
        return None

    # ------------------------------------------------------------------
    # Approval thread + RPG inbox (05-18-approval-thread-and-rpg)
    # ------------------------------------------------------------------

    def register_revision_handler(
        self,
        action_type: str,
        handler: Callable[[ApprovalRequest, str], ApprovalRequest],
    ) -> None:
        """Register a revision handler for one ``action_type``.

        The handler is invoked when a player ``request_revision`` lands
        with a counter-proposal hint. Signature:

            handler(original_approval: ApprovalRequest, hint_text: str)
                -> ApprovalRequest

        The handler must **persist** the returned ``ApprovalRequest`` (via
        ``self.approvals.create`` or equivalent) and set its
        ``predecessor_id`` to ``original_approval.id`` so ``list_thread``
        can link the two rows.

        Each ``action_type`` may have at most one handler; re-registering
        replaces the previous one (handy for tests and for swapping in an
        LLM-driven replacement at boot).
        """
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._revision_handlers[action_type] = handler

    def _default_revision_handler(
        self,
        original: ApprovalRequest,
        hint: str,
    ) -> ApprovalRequest:
        """Fallback when no caller-specific handler is registered.

        Copies the original ``payload``, stamps the player's counter
        proposal into ``payload['revision_hint']``, links via
        ``predecessor_id``, and re-submits as ``pending`` for player
        approval. **Critically**, the new approval is created with the
        same ``action_type`` but the handler does *not* trigger another
        revision pathway — that would loop.
        """
        new_payload = {**(original.payload or {}), "revision_hint": hint}
        successor = ApprovalRequest(
            action_type=original.action_type,
            summary=f"[Revised] {original.summary}",
            payload=new_payload,
            directive_id=original.directive_id,
            project_id=original.project_id,
            requested_by=original.requested_by,
            severity=original.severity,
            predecessor_id=original.id,
        )
        return self.approvals.create(successor)

    def request_approval_revision(
        self,
        request_id: str,
        counter: str,
        by_type: str = "user",
        by_id: str | None = None,
        comment_body: str | None = None,
    ) -> dict | None:
        """Player counter-proposal flow.

        1. Original approval -> ``revision_requested`` (terminal).
        2. The counter text lands as a comment on the original.
        3. The action-type's revision handler (or the default fallback)
           creates a fresh ``pending`` approval that links back via
           ``predecessor_id``.

        Returns a dict with ``original`` and ``successor`` payloads, or
        ``None`` if the original was not found.
        """
        original = self.approvals.get(request_id)
        if original is None:
            return None
        # Use comment_body for the "additional context" if provided; the
        # ``counter`` text is always preserved as its own thread comment.
        original_after = self.approvals.request_revision(
            request_id=request_id,
            comment_body=counter,
            by_type=by_type,
            by_id=by_id,
        )
        if original_after is None:
            return None
        if comment_body:
            self.approvals.add_comment(
                approval_id=request_id,
                body=comment_body,
                by_type=by_type,
                by_id=by_id,
            )
        handler = self._revision_handlers.get(
            original.action_type, self._default_revision_handler
        )
        successor = handler(original, counter)
        self.audit.record(
            "approval.revision_requested",
            "Player requested revision",
            detail={
                "approval_id": original.id,
                "successor_id": successor.id,
                "action_type": original.action_type,
            },
            directive_id=original.directive_id,
            project_id=original.project_id,
        )
        return {
            "original": original_after.model_dump(mode="json"),
            "successor": successor.model_dump(mode="json"),
        }

    def snooze_approval(
        self,
        request_id: str,
        minutes: int,
        by_type: str = "user",
        by_id: str | None = None,
        comment_body: str | None = None,
    ) -> dict | None:
        """Snooze an approval; the watchdog auto-unsnoozes when due."""
        request = self.approvals.snooze(
            request_id=request_id,
            minutes=minutes,
            by_type=by_type,
            by_id=by_id,
            comment_body=comment_body,
        )
        if request is None:
            return None
        self.audit.record(
            "approval.snoozed",
            f"Approval snoozed for {minutes}m",
            detail={
                "approval_id": request.id,
                "minutes": minutes,
                "snoozed_until": (
                    request.snoozed_until.isoformat()
                    if request.snoozed_until
                    else None
                ),
            },
            directive_id=request.directive_id,
            project_id=request.project_id,
        )
        return request.model_dump(mode="json")

    def cancel_approval(
        self,
        request_id: str,
        reason: str | None = None,
        by_type: str = "user",
        by_id: str | None = None,
        comment_body: str | None = None,
    ) -> dict | None:
        """Cancel an approval (terminal): the player says 'don't pursue this'."""
        request = self.approvals.cancel(
            request_id=request_id,
            reason=reason,
            by_type=by_type,
            by_id=by_id,
            comment_body=comment_body,
        )
        if request is None:
            return None
        self.audit.record(
            "approval.cancelled",
            "Approval cancelled",
            detail={
                "approval_id": request.id,
                "reason": reason,
            },
            directive_id=request.directive_id,
            project_id=request.project_id,
        )
        return request.model_dump(mode="json")

    def comment_on_approval(
        self,
        request_id: str,
        body: str,
        by_type: str = "user",
        by_id: str | None = None,
    ) -> dict | None:
        """Append a free-form comment to an approval thread.

        Allowed on any state — terminal or not — so the player can leave
        retrospective notes on a closed thread.
        """
        if self.approvals.get(request_id) is None:
            return None
        comment = self.approvals.add_comment(
            approval_id=request_id,
            body=body,
            by_type=by_type,
            by_id=by_id,
        )
        return comment.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Company targets + team feasibility review
    # ------------------------------------------------------------------

    def get_targets(self) -> CompanyTargets:
        """Return the authoritative targets (``agreed`` > founder fallback)."""
        return get_company_targets(self.db)

    def get_targets_bundle(self) -> TargetsBundle:
        """Return all three states + the review approval id."""
        return get_targets_bundle(self.db)

    def set_targets(self, targets: CompanyTargets) -> CompanyTargets:
        """Persist a target snapshot keyed by ``targets.source``."""
        return set_company_targets(self.db, targets)

    # ------------------------------------------------------------------
    # UI preferences (theme system, feature A — 05-27)
    # ------------------------------------------------------------------

    def get_ui_preferences(self) -> UIPreferences:
        """Return the founder's dashboard appearance preferences (or defaults)."""
        return get_ui_preferences(self.db)

    def set_ui_preferences(
        self,
        *,
        theme_id: str | None = None,
        auto_enabled: bool | None = None,
        reduce_motion: str | None = None,
    ) -> UIPreferences:
        """Patch UI preferences; ``ValueError`` on a bad ``reduce_motion``."""
        prefs = set_ui_preferences(
            self.db,
            theme_id=theme_id,
            auto_enabled=auto_enabled,
            reduce_motion=reduce_motion,
        )
        self.audit.record(
            event_type="preferences.updated",
            action="Updated UI preferences",
            detail=prefs.model_dump(mode="json"),
        )
        return prefs

    def _compose_targets_summary(self) -> str:
        """One-paragraph string injected into CEO/CFO/CoS system prompts.

        Reads the authoritative targets + current cash so the agent sees
        the same numbers the watchdog uses to fire ``runway_alert``.
        """
        try:
            cash = self.ledger.get_balance()
        except Exception:  # pragma: no cover — ledger errors don't kill prompts
            cash = None
        return compose_targets_summary(self.get_targets(), cash=cash)

    def _compose_glossary_summary(self) -> str:
        """Render the company glossary as a system-prompt block.

        Returns ``""`` when the glossary is empty so callers can splice
        the value into prompts unconditionally without an awkward blank
        section. Used by CEO classify / CFO target review / CoS distill
        / CoS retrospect prompts. Reads via the cached
        :class:`GlossaryService` so concurrent edits don't tear the snapshot.

        Glossary-and-drift-detection task 05-19.
        """
        try:
            glossary = self.glossary.load()
        except Exception:  # pragma: no cover — never let glossary kill prompts
            return ""
        return glossary.compose_summary()

    # ------------------------------------------------------------------
    # Company glossary (glossary-and-drift-detection task 05-19)
    # ------------------------------------------------------------------

    def list_glossary(self) -> list[dict[str, Any]]:
        """Return every glossary entry as a JSON-ready dict."""
        return [
            entry.model_dump(mode="json") for entry in self.glossary.list_terms()
        ]

    def get_glossary_term(self, term: str) -> dict[str, Any] | None:
        """Look up one term (case-insensitive). Returns ``None`` if missing."""
        entry = self.glossary.get(term)
        return entry.model_dump(mode="json") if entry is not None else None

    def add_glossary_term(
        self,
        term: str,
        definition: str,
        forbidden_synonyms: list[str] | None = None,
        added_by: str = "founder",
        source_episode_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new glossary term. Raises ``ValueError`` if it exists."""
        # The service uses Literal["founder","cos_proposal","template"]; we
        # validate the string here so callers get a clear error instead of
        # a deep Pydantic ValidationError.
        valid_sources = {"founder", "cos_proposal", "template"}
        if added_by not in valid_sources:
            raise ValueError(
                f"added_by must be one of {sorted(valid_sources)}, got {added_by!r}"
            )
        entry = self.glossary.add(
            term=term,
            definition=definition,
            forbidden_synonyms=forbidden_synonyms,
            added_by=added_by,  # type: ignore[arg-type]
            source_episode_id=source_episode_id,
        )
        self.audit.record(
            event_type="glossary.term_added",
            action=f"Added glossary term {entry.term!r}",
            detail={
                "term": entry.term,
                "added_by": entry.added_by,
                "forbidden_synonyms": entry.forbidden_synonyms,
                "source_episode_id": entry.source_episode_id,
            },
        )
        return entry.model_dump(mode="json")

    def update_glossary_term(
        self,
        term: str,
        definition: str | None = None,
        forbidden_synonyms: list[str] | None = None,
    ) -> dict[str, Any]:
        """Mutate an existing glossary entry."""
        entry = self.glossary.update(
            term,
            definition=definition,
            forbidden_synonyms=forbidden_synonyms,
        )
        self.audit.record(
            event_type="glossary.term_updated",
            action=f"Updated glossary term {entry.term!r}",
            detail={
                "term": entry.term,
                "definition": entry.definition,
                "forbidden_synonyms": entry.forbidden_synonyms,
            },
        )
        return entry.model_dump(mode="json")

    def remove_glossary_term(self, term: str) -> bool:
        """Drop one glossary term. Returns ``True`` if a row was deleted."""
        removed = self.glossary.remove(term)
        if removed:
            self.audit.record(
                event_type="glossary.term_removed",
                action=f"Removed glossary term {term!r}",
                detail={"term": term},
            )
        return removed

    def _runway_snapshot(self) -> dict[str, Any] | None:
        """Build the dict the watchdog's runway scanner consumes.

        Returns ``None`` when no agreed/founder deadline is set, when the
        ledger or targets table is unreachable, or when burn rate hasn't
        produced enough signal yet. The watchdog treats ``None`` as
        "skip this tick"; it never raises.
        """
        try:
            targets = self.get_targets()
        except Exception:  # noqa: BLE001
            return None
        if not targets.deadline:
            return None
        try:
            cash = self.ledger.get_balance()
        except Exception:  # noqa: BLE001
            cash = 0.0
        try:
            burn_rate = self.ledger.recent_burn_rate(window_hours=24)
        except Exception:  # noqa: BLE001
            burn_rate = 0.0
        return {
            "cash": float(cash),
            "burn_rate": float(burn_rate),
            "deadline": targets.deadline,
            "targets": targets.model_dump(mode="json"),
        }

    def _finalize_glossary_review(
        self,
        request: ApprovalRequest,
        *,
        outcome: str,
    ) -> None:
        """Post-resolve hook for ``glossary_review`` approvals.

        On approve → close the matching ``glossary_drift_alert`` health
        events as resolved and write a ``glossary.drift_resolved`` audit
        event. No glossary mutation occurs by default — the founder is
        acknowledging that the drift was real and the canonical terms
        are correct as-is; if they wanted to change the canonical word
        they would edit the glossary directly.

        On reject → also close the health events, but the audit detail
        tags the outcome as ``"dismissed_false_positive"`` so distillation
        learns this synonym pair is fine in practice.
        """
        payload = request.payload or {}
        project_id = request.project_id
        drift_count = 0
        drifts = payload.get("drifts")
        if isinstance(drifts, list):
            drift_count = len(drifts)

        # Close matching open ``glossary_drift_alert`` rows. We match by
        # ``project_id`` + ``approval_id`` to avoid clobbering unrelated
        # drift alerts on the same project.
        closed = 0
        if project_id is not None:
            try:
                events = self.health_events.list_for_project(project_id)
            except Exception:  # pragma: no cover - defensive
                events = []
            for ev in events:
                if ev.get("kind") != "glossary_drift_alert":
                    continue
                if ev.get("status") != "open":
                    continue
                detail = ev.get("detail") if isinstance(ev.get("detail"), dict) else {}
                if detail.get("approval_id") and detail.get("approval_id") != request.id:
                    continue
                try:
                    self.health_events.resolve(
                        event_id=ev["id"],
                        action="continue" if outcome == "approved" else "dismiss",
                        resolved_by="founder",
                    )
                    closed += 1
                except Exception:  # pragma: no cover - defensive
                    continue

        self.audit.record(
            event_type=(
                "glossary.drift_resolved"
                if outcome == "approved"
                else "glossary.drift_dismissed"
            ),
            action=(
                f"Founder {'accepted' if outcome == 'approved' else 'dismissed'} "
                f"{drift_count} drift hit(s) for approval {request.id}"
            ),
            detail={
                "approval_id": request.id,
                "outcome": outcome,
                "drift_count": drift_count,
                "events_closed": closed,
            },
            project_id=project_id,
        )

    # ------------------------------------------------------------------
    # Glossary drift scan + approval (glossary-and-drift-detection 05-19)
    # ------------------------------------------------------------------

    def _run_glossary_drift_scan(
        self,
        *,
        project_id: str,
        reflections: list[Any],
    ) -> dict[str, Any] | None:
        """Detect glossary drift on the just-finished retrospective output.

        Pulls the audit events tied to this project (for stringified
        ``detail`` scans) and the project's decisions, then defers to
        :func:`kompany.agents.cos_glossary_scan.scan_drift`.

        If any drift is detected:

        * Write one ``glossary_drift_alert`` health event via
          :meth:`Watchdog.record_glossary_drift`.
        * Create one ``approval_request(action_type='glossary_review')``
          carrying ``drifts`` + ``suggested_corrections``.
        * Mirror an audit event for the timeline.

        Returns a summary dict ``{"drift_count", "approval_id"}`` or
        ``None`` when the scan was a no-op (empty glossary or zero hits).
        """
        from kompany.agents.cos_glossary_scan import (
            build_suggested_corrections,
            scan_drift,
        )

        glossary = self.glossary.load()
        if len(glossary) == 0:
            return None

        # Pull decisions and audit events tied to the project. We use the
        # raw DB rows (rather than the materialised episode payload)
        # because materialisation hasn't happened yet — that's the point
        # of running the scan first.
        decisions_rows = self.db.execute(
            "SELECT id, agents_involved, result FROM decisions "
            "WHERE result LIKE ? ORDER BY timestamp",
            (f"%{project_id}%",),
        ).fetchall()
        decisions: list[dict[str, Any]] = []
        for row in decisions_rows:
            try:
                result_obj = (
                    __import__("json").loads(row["result"]) if row["result"] else {}
                )
            except (TypeError, ValueError):
                result_obj = {}
            try:
                agents_involved = __import__("json").loads(row["agents_involved"])
                if not isinstance(agents_involved, list):
                    agents_involved = []
            except (TypeError, ValueError):
                agents_involved = []
            summary_text = (
                result_obj.get("message")
                if isinstance(result_obj, dict) and "message" in result_obj
                else str(result_obj)
            )
            decisions.append({
                "summary": str(summary_text or ""),
                "agents_involved": agents_involved,
            })

        audit_rows = self.db.execute(
            "SELECT event_type, detail FROM audit_log WHERE project_id = ? "
            "ORDER BY id",
            (project_id,),
        ).fetchall()
        audit_events: list[dict[str, Any]] = []
        for row in audit_rows:
            detail_obj: dict[str, Any] = {}
            if row["detail"]:
                try:
                    parsed = __import__("json").loads(row["detail"])
                    if isinstance(parsed, dict):
                        detail_obj = parsed
                    else:
                        detail_obj = {"value": parsed}
                except (TypeError, ValueError):
                    detail_obj = {"raw": row["detail"]}
            audit_events.append({
                "type": row["event_type"],
                "detail": detail_obj,
            })

        drifts = scan_drift(
            glossary=glossary,
            reflections=reflections,
            decisions=decisions,
            audit_events=audit_events,
        )
        if not drifts:
            return None

        suggestions = build_suggested_corrections(drifts, glossary)
        payload = {
            "project_id": project_id,
            "drifts": [d.model_dump(mode="json") for d in drifts],
            "suggested_corrections": suggestions,
        }
        # Build a short founder-facing summary line for the inbox.
        roles_seen = ", ".join(sorted({d.agent_role for d in drifts}))
        summary = (
            f"Glossary drift in episode {project_id}: "
            f"{len(drifts)} hit(s) across {roles_seen or 'unknown agents'}."
        )
        approval = self.approvals.create(
            ApprovalRequest(
                action_type="glossary_review",
                summary=summary,
                payload=payload,
                project_id=project_id,
                severity="medium",
                requested_by="cos",
            )
        )
        # Record the matching health event after the approval exists so we
        # can cross-link them in both directions.
        self.watchdog.record_glossary_drift(
            episode_id=project_id,
            drifts=drifts,
            project_id=project_id,
            approval_id=approval.id,
        )
        self.audit.record(
            event_type="glossary.drift_detected",
            action=f"CoS detected {len(drifts)} glossary drift hit(s)",
            detail={
                "project_id": project_id,
                "drift_count": len(drifts),
                "approval_id": approval.id,
                "roles": sorted({d.agent_role for d in drifts}),
            },
            project_id=project_id,
        )
        return {
            "drift_count": len(drifts),
            "approval_id": approval.id,
        }

    def _glossary_review_revision_handler(
        self,
        original: ApprovalRequest,
        hint: str,
    ) -> ApprovalRequest:
        """Founder counter-proposal on a glossary drift alert.

        Copies the payload, stamps the hint into ``revision_hint`` and
        re-issues as ``pending``. The founder can then approve a slimmer
        list ("just the customer correction, drop the MRR one") on the
        next pass. We deliberately don't try to parse partial-accept
        instructions from free-form text — the founder gets another
        round of approve / reject / revise on the successor.
        """
        new_payload = {**(original.payload or {}), "revision_hint": hint}
        successor = ApprovalRequest(
            action_type=original.action_type,
            summary=f"[Revised] {original.summary}",
            payload=new_payload,
            directive_id=original.directive_id,
            project_id=original.project_id,
            requested_by=original.requested_by,
            severity=original.severity,
            predecessor_id=original.id,
        )
        return self.approvals.create(successor)

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
            debate_id=debate_id,
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
