"""KompanyEngine — the single entry point for all interfaces."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from kompany.agents.registry import AgentRegistry
from kompany.config.settings import KompanySettings
from kompany.core.answer_context import compose_answer_context
from kompany.core.autonomy import AutonomyGate
from kompany.core.directive import (
    Directive,
    DirectiveResult,
    DirectiveStatus,
    DirectiveType,
)
from kompany.core.event_hub import get_event_hub
from kompany.core.credential_broker import (
    CredentialBrokerClient,
    HttpCredentialBroker,
    UnavailableCredentialBroker,
)
from kompany.core.run_context import current_run_id, run_scope
from kompany.core.subscription_fee import book_subscription_fee_if_due
from kompany.core.anima import Anima
from kompany.core.ticker import Ticker
from kompany.core.watchdog import LLMUnavailable, Watchdog
from kompany.llm.client import LLMClient
from kompany.llm.cost_tracker import CostTracker
from kompany.notifications import build_notifier
from kompany.remote import RemoteCommandRequest, RemoteCommandResult, parse_remote_text
from kompany.state.agent_status import AgentStatusStore
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.checkpoints import CheckpointStore
from kompany.state.conversation import ConversationStore
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
    SESSION_TERMINAL_STATUSES,
    SessionStatus,
)
from kompany.state.backup import BackupManager
from kompany.state.debates import Debates
from kompany.state.episodes import Episodes
from kompany.state.health_events import HealthEvents
from kompany.state.projects import Projects
from kompany.state.delegations import DelegationStore
from kompany.state.memory import AgentMemory
from kompany.state.skills import SkillStore
from kompany.state.daemon_ticks import DaemonTickStore
from kompany.state.intake_queue import IntakeQueueStore
from kompany.core.lane_registry import LaneRegistry
from kompany.core.lane_dispatcher import LaneDispatcher
from kompany.core.outward_lane import OutwardLane
from kompany.state.self_update_proposals import SelfUpdateProposalStore
from kompany.state.runtime import RuntimeStateStore
from kompany.state.remote_replay import RemoteReplayStore
from kompany.state.shadow_costs import ShadowCostStore
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
from kompany.state.outward_policies import OutwardActionPolicyStore
from kompany.state.tool_authorization import ToolAuthorizationStore


from kompany.core.directive_proposal import DirectiveProposalMixin
from kompany.core.target_review import TargetReviewMixin

from kompany.core.engine_parts import (
    AgenticChatMixin,
    SkillCrystallizationMixin,
    CompanyLifecycleMixin,
    ProjectExecutionMixin,
    LearningMixin,
    DistillationMixin,
    EngineOpsMixin,
    RuntimeOpsMixin,
    ObservabilityMixin,
    FounderSurfacesMixin,
    DirectiveProcessingMixin,
    ChannelRoutingMixin,
    CredentialBrokerMixin,
    ChannelActionsMixin,
    ApprovalsMixin,
    GovernanceMixin,
    DirectiveHandlersMixin,
)

log = logging.getLogger(__name__)


class KompanyEngine(
    AgenticChatMixin,
    SkillCrystallizationMixin,
    CompanyLifecycleMixin,
    ProjectExecutionMixin,
    LearningMixin,
    DistillationMixin,
    EngineOpsMixin,
    RuntimeOpsMixin,
    ObservabilityMixin,
    FounderSurfacesMixin,
    DirectiveProcessingMixin,
    ChannelRoutingMixin,
    CredentialBrokerMixin,
    ChannelActionsMixin,
    ApprovalsMixin,
    GovernanceMixin,
    DirectiveHandlersMixin,
    TargetReviewMixin,
    DirectiveProposalMixin,
):
    """Core engine. All interfaces (CLI, API, MCP, SDK) call this."""

    def __init__(self, config_path: str | None = None):
        # Remembered so ModelSource settings mutations persist to the
        # same YAML this engine was loaded from (model_source_ops).
        self._config_path = config_path
        self.settings = KompanySettings.load(config_path)
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.settings.data_dir)
        self.ledger = Ledger(self.db)
        self.journal = Journal(self.db)
        self.projects = Projects(self.db)
        self.delegations = DelegationStore(self.db, self.projects)
        self.memory = AgentMemory(self.db)
        self.skills = SkillStore(self.db)
        self.audit = AuditLog(self.db)
        self.debates = Debates(self.db)
        self.episodes = Episodes(self.db)
        self.health_events = HealthEvents(self.db)
        self.approvals = ApprovalRequests(self.db)
        self.channel = ConversationStore(self.db)
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
        broker_backend = (
            HttpCredentialBroker(
                self.settings.credential_broker_endpoint,
                auth_token=self.settings.credential_broker_token,
                timeout_seconds=(
                    self.settings.credential_broker_timeout_seconds
                ),
            )
            if self.settings.credential_broker_endpoint
            else UnavailableCredentialBroker()
        )
        self.credential_broker = CredentialBrokerClient(broker_backend)
        self._apply_vault_credentials()
        self.tool_authorization = ToolAuthorizationStore(self.db)
        self.outward_policies = OutwardActionPolicyStore(self.db)
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
        # ``settings`` + ``shadow_costs`` carry the billing_mode rules
        # (06-11-harness-execution-leg D2): subscription-billed calls
        # book shadow value instead of a real expense.
        self.shadow_costs = ShadowCostStore(self.db)
        self.cost_tracker = CostTracker(
            self.ledger,
            event_hub=get_event_hub(),
            settings=self.settings,
            shadow_costs=self.shadow_costs,
        )
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
            agent_status=self.agent_status,
        )

        # Daemon tick loop (06-12-daemon-tick-loop PR1): the autonomous
        # heartbeat that advances work with no founder session open.
        # Tick logic lives in core/ticker.py (engine.py is over the cap).
        self.daemon_ticks = DaemonTickStore(self.db)
        # Concurrent resilient runtime (ADR-0005): intake queue (dev-inbox),
        # lane registry (own-lease, no double-run), and the dispatcher the
        # ticker delegates ``advance`` to. ``ensure_default`` seeds a single
        # ``main`` lane so behaviour stays identical to the pre-lane ticker.
        self.intake_queue = IntakeQueueStore(self.db)
        self.lane_registry = LaneRegistry(self.db)
        self.lane_registry.ensure_default()
        self.lane_dispatcher = LaneDispatcher(
            engine=self,
            registry=self.lane_registry,
            intake=self.intake_queue,
        )
        # Outward-execution lane (ADR-0008 Step 4): drains the outward queue,
        # resolves auto/gated, runs pre-flight, then executes via a
        # project-supplied OutwardExecutor or PARKS for `kompany approve`. The
        # engine ships NO executor; ``outward_executors`` discovers [] until a
        # project installs one. Empty queue ⇒ dispatch_once is a no-op.
        try:
            from kompany.plugins.loader import registered as _registered

            self.outward_executors = _registered("outward_executor")
        except Exception:  # noqa: BLE001 — a broken plugin scan must not block boot
            self.outward_executors = []
        self.outward_lane = OutwardLane(
            engine=self, registry=self.lane_registry
        )
        self.self_update_proposals = SelfUpdateProposalStore(self.db)
        self.ticker = Ticker(
            engine=self,
            ticks=self.daemon_ticks,
            tick_interval_seconds=self.settings.tick_interval_seconds,
            auto_execute=self.settings.daemon_auto_execute,
        )
        # ADR-0008 Step 4: the outward lane runs as a ticker action so the
        # daemon acts outward unattended. No-op on an empty queue; honours
        # suspend via its own runtime gate.
        self.ticker.actions.append(
            ("outward", self.outward_lane.dispatch_once)
        )

        # Anima persona layer (06-12-anima-persona): emotion + diary tick
        # intents appended to the ticker's actions list (PRD D4 — the
        # suspend gate already precedes every action). Logic lives in
        # core/anima.py (engine.py is over the cap). The provisional
        # glossary term is seeded idempotently (one cheap read when it
        # already exists, PRD D1).
        self.anima: Anima | None = None
        if self.settings.anima_enabled:
            self.anima = Anima(self)
            self.ticker.actions.append(
                ("anima_emotion", self.anima.emotion_tick)
            )
            if self.settings.anima_diary_enabled:
                self.ticker.actions.append(
                    ("anima_diary", self.anima.diary_tick)
                )
            try:
                self.anima.ensure_glossary_entry()
            except Exception:  # noqa: BLE001 — glossary seed must never block boot
                pass

        # Bidirectional channels (06-12-channels). Telegram worker starts
        # with the engine's background workers iff a bot token is set
        # (PRD D2); email-in piggybacks on the ticker cadence (PRD D4) so
        # the daemon needs no extra worker. Logic lives in channels/
        # (engine.py is over the cap).
        self.telegram_worker = None
        if self.settings.telegram_bot_token:
            from kompany.channels.telegram import TelegramWorker

            self.telegram_worker = TelegramWorker(engine=self)
        self.email_poller = None
        if self.settings.email_imap_host and self.settings.email_imap_user:
            from kompany.channels.email_in import ImapPoller

            self.email_poller = ImapPoller(engine=self)
            self.ticker.actions.append(
                ("email_poll", self.email_poller.tick_action)
            )

        # OAuth-subscription token sink (06-16-agentic-chat-engine P3).
        # Backed by the same encrypted credential vault; threaded into the
        # LLMClient so ``chatgpt-oauth:*`` calls authenticate with the
        # stored (auto-refreshed) bearer token. `kompany auth openai`
        # populates it; absent a login the provider path raises a clear
        # "run kompany auth openai" error.
        from kompany.llm.oauth import OAuthTokenStore

        self.oauth_token_store = OAuthTokenStore(self.credentials)
        self.llm = LLMClient(
            settings=self.settings,
            cost_tracker=self.cost_tracker,
            provider_error_handler=self._handle_provider_error,
            audit_log=self.audit,
            watchdog=self.watchdog,
            silent_timeout_seconds=self._get_int_config(
                "llm_silent_timeout_seconds", default=90
            ),
            # ADR-0005: a lane-worker must survive a single-model outage.
            fallback_models=self.settings.fallback_model_pool(),
            oauth_token_store=self.oauth_token_store,
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
        # ADR-0007: a founder "revise" on a held C-suite review stamps the
        # task with the feedback and re-files a fresh pending review card.
        from kompany.core.csuite_review import revision_requested_csuite_review

        self.register_revision_handler(
            "csuite_review",
            lambda original, hint: revision_requested_csuite_review(
                self, original, hint
            ),
        )

    def get_delegation(self, delegation_id: str):
        return self.delegations.get(delegation_id)

    def cancel_delegation(self, delegation_id: str):
        delegation = self.delegations.cancel(delegation_id)
        if delegation is None:
            raise ValueError(f"delegation {delegation_id!r} not found")
        self.audit.record(
            "delegation.cancelled",
            "CEO cancelled background delegation",
            detail={"delegation_id": delegation_id},
            agent_role="ceo",
            directive_id=delegation.directive_id,
            project_id=delegation.project_id,
        )
        get_event_hub().publish(
            "delegation.milestone",
            {
                "delegation_id": delegation_id,
                "status": "cancelled",
                "session_id": delegation.session_id,
                "project_id": delegation.project_id,
            },
        )
        return delegation

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
            # Project only credentials the settings model actually declares.
            # ``ALLOWED_CREDENTIALS`` has grown to cover integration creds
            # (resend_api_key, smtp_*, ...) that don't appear on
            # ``KompanySettings``; setattr-ing them raised ValueError and
            # crashed engine boot once those credentials were stored.
            declared = set(getattr(type(self.settings), "model_fields", {}) or ())
            for name in sorted(ALLOWED_CREDENTIALS):
                if declared and name not in declared:
                    continue
                if getattr(self.settings, name, ""):
                    continue
                value = self.credentials.get(name)
                if not value:
                    continue
                try:
                    setattr(self.settings, name, value)
                except (ValueError, TypeError):
                    # Non-pydantic test fakes or fields not on the model —
                    # skip silently, the vault remains source of truth via
                    # credentials.get().
                    continue
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
        for attr, key in [
            ("company_name", "company_name"),
            ("company_goal", "company_goal"),
            ("company_stage", "company_stage"),
            ("company_time_horizon", "company_time_horizon"),
            ("company_exclusions", "company_exclusions"),
        ]:
            if not getattr(self.settings, attr, ""):
                try:
                    row = self.db.execute(
                        "SELECT value FROM company_config WHERE key = ?", (key,)
                    ).fetchone()
                except Exception:  # noqa: BLE001
                    row = None
                if row and row["value"]:
                    setattr(self.settings, attr, row["value"])
        # Founder profile + rules (#6/#7): mirror the company_config JSON
        # rows into settings so every agent prompt (BaseAgent reads only
        # settings) carries the founder context from boot.
        from kompany.core import founder_config

        self.settings.founder_profile = founder_config.get_founder_profile(self)
        self.settings.founder_rules = founder_config.get_founder_rules(self)

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

    async def start(self) -> None:
        """Start engine background workers (watchdog + ticker + channels).

        Runs one-shot startup reconciliation first (Stage A deployment
        plan: session-persistence gap) — this is the real daemon boot
        entry point, called exactly once per process lifetime, unlike a
        one-shot CLI ``KompanyEngine()`` construction that may run
        alongside an already-live daemon. Any task still ``active``/
        ``in_progress`` or ``agent_status`` row still ``working`` at this
        exact moment is provably orphaned from a previous process.
        """
        try:
            self.watchdog.reconcile_on_startup()
        except Exception:  # noqa: BLE001 — a reconciliation bug must never block boot
            log.exception("watchdog.reconcile_on_startup failed")
        self.watchdog.start()
        self.ticker.start()
        if self.telegram_worker is not None:
            self.telegram_worker.start()

    async def stop(self) -> None:
        """Stop engine background workers."""
        await self.watchdog.stop()
        await self.ticker.stop()
        if self.telegram_worker is not None:
            await self.telegram_worker.stop()
