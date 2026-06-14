"""Pydantic models for all persistent state objects."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _short_id() -> str:
    return uuid4().hex[:8]


class LedgerCategory(str, Enum):
    INCOME = "income"
    EXPENSE = "expense"
    AI_COST = "ai_cost"
    ALLOCATION = "allocation"
    REFUND = "refund"
    # Monthly ModelSource subscription fee — the real recurring expense
    # for subscription billing (06-11-harness-execution-leg D2). Booked
    # idempotently per calendar month by ``engine.heartbeat_once``.
    SUBSCRIPTION_FEE = "subscription_fee"
    # Real money an executed tool action reports spending (#4/#5 action
    # pipeline). Booked by core/tool_actions.py on approved execution.
    TOOL_COST = "tool_cost"


class LedgerEntry(BaseModel):
    id: int | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    amount: float  # positive = income, negative = expense
    balance_after: float = 0.0
    description: str
    category: LedgerCategory
    directive_id: str | None = None
    project_id: str | None = None
    approved_by: str | None = None  # "auto" | "ceo" | "master"


class ProjectStatus(str, Enum):
    # DRAFT is a real state: Templates.apply stages suggested directives
    # as 'draft' rows and the onboarding first-move step keeps the
    # unpicked directives as drafts. It was historically written as a
    # literal string that bypassed this enum, so any code path that
    # constructed a Project model from a draft row (projects.get,
    # _row_to_project, episode materialization) raised a pydantic enum
    # validation error — which silently killed the post-onboarding
    # kickoff mid-run. Modelling it here fixes that class of crash.
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"   # founder abandoned the plan


class ProjectType(str, Enum):
    REVENUE = "revenue"
    OPERATIONAL = "operational"
    STRATEGIC = "strategic"


class Project(BaseModel):
    id: str = Field(default_factory=_short_id)
    name: str
    type: ProjectType
    status: ProjectStatus = ProjectStatus.ACTIVE
    target_amount: float | None = None
    funded_amount: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    triggers_directive_id: str | None = None
    plan: dict[str, Any] = Field(default_factory=dict)
    assigned_agents: list[str] = Field(default_factory=list)


class TaskStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"   # truly executed — a real tool/integration acted
    DELIVERED = "delivered"   # asset produced; founder must act to finish it
    BLOCKED = "blocked"       # needs a connection/approval the agent lacks
    FAILED = "failed"
    CANCELLED = "cancelled"   # project abandoned — task withdrawn, not failed

    @classmethod
    def terminal(cls) -> set["TaskStatus"]:
        """States a task does not leave on its own."""
        return {cls.COMPLETED, cls.DELIVERED, cls.BLOCKED, cls.FAILED, cls.CANCELLED}


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"
    SNOOZED = "snoozed"
    CANCELLED = "cancelled"


# Severity values for approval requests. Kept as a frozenset (not Enum) so
# string literals from CLI / API / SQL writes don't need ``.value`` plumbing.
APPROVAL_SEVERITIES: frozenset[str] = frozenset({
    "info",
    "low",
    "medium",
    "high",
    "critical",
})

# Terminal statuses — no outgoing transitions. ``snoozed`` is *not* terminal:
# the watchdog auto-unsnoozes it back to ``pending`` when ``snoozed_until``
# lapses.
APPROVAL_TERMINAL_STATUSES: frozenset[ApprovalStatus] = frozenset({
    ApprovalStatus.APPROVED,
    ApprovalStatus.REJECTED,
    ApprovalStatus.REVISION_REQUESTED,
    ApprovalStatus.CANCELLED,
})


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=_short_id)
    status: ApprovalStatus = ApprovalStatus.PENDING
    action_type: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    directive_id: str | None = None
    project_id: str | None = None
    requested_by: str | None = None
    resolved_by: str | None = None
    resolution_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    # Approval thread + RPG inbox additions (05-18-approval-thread-and-rpg).
    # All optional with safe defaults so existing call sites stay legal.
    severity: str = "medium"
    predecessor_id: str | None = None
    snoozed_until: datetime | None = None
    snoozed_by: str | None = None


class ApprovalComment(BaseModel):
    """One comment on an approval thread.

    ``by_type`` is one of ``user`` / ``agent`` / ``system``. ``by_id`` is the
    agent role (``cfo``, etc.) for agent comments, the player alias or NULL
    for user comments, and NULL for system-generated audit notes.
    """

    id: str = Field(default_factory=_short_id)
    approval_id: str
    by_type: str  # "user" | "agent" | "system"
    by_id: str | None = None
    body: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# CEO channel — founder<->team conversation sessions + turns
# (06-03-ceo-channel). A session is the parent row (cf. approval_requests);
# turns are the ordered children (cf. approval_comments). The CEO auto-routes
# each founder message into execute / clarify / answer; the session lifecycle
# tracks where that conversation is.
# ---------------------------------------------------------------------------


class SessionStatus(str, Enum):
    OPEN = "open"
    CLARIFYING = "clarifying"
    DISPATCHED = "dispatched"      # terminal — founder intent executed
    ANSWERED = "answered"          # terminal — pure question answered
    ABANDONED = "abandoned"        # terminal — founder/CEO gave up
    GATED = "gated"                # non-terminal — spend-gate pause, resumes on GO
    PROPOSED = "proposed"          # non-terminal — CEO answer contained a proposal,
                                   # awaiting founder GO to execute it


# Terminal session statuses — a closed session rejects further sends.
# ``gated`` and ``proposed`` are intentionally NOT terminal (both resume on GO).
SESSION_TERMINAL_STATUSES: frozenset[SessionStatus] = frozenset({
    SessionStatus.DISPATCHED,
    SessionStatus.ANSWERED,
    SessionStatus.ABANDONED,
})

# Turn ``role`` enumeration. ``founder`` = the Master's message; ``ceo`` = the
# conductor's reply (clarify question / answer / final dispatch summary).
TURN_ROLES: frozenset[str] = frozenset({"founder", "ceo"})

# Turn ``kind`` enumeration. Mirrors the cost-visibility surfaces:
# ``message`` (founder input), ``clarify_question`` (CEO asks back),
# ``preview`` (PR2 gate plan + est cost), ``final`` (CEO terminal reply),
# ``progress_summary`` (collapsed STREAM summary).
TURN_KINDS: frozenset[str] = frozenset({
    "message",
    "clarify_question",
    "preview",
    "final",
    "progress_summary",
})


class ConversationSession(BaseModel):
    """A CEO-channel conversation session (parent of turns)."""

    id: str = Field(default_factory=_short_id)
    state: SessionStatus = SessionStatus.OPEN
    route: str | None = None  # last classified route: execute|clarify|answer
    clarify_turns: int = 0    # count of clarify questions asked this session
    directive_id: str | None = None
    project_id: str | None = None
    approval_id: str | None = None
    run_id: str | None = None
    # Server-side scratch payload for a session. PR2 stores the gated
    # directive snapshot here (raw_input + CEO classification) so a founder
    # GO survives an engine restart — a gated session is a parked decision
    # that may sit for hours/days, and the desktop app restarts the engine
    # routinely. Empty dict for non-gated sessions.
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None


class ConversationTurn(BaseModel):
    """One turn in a CEO-channel session (child of a session)."""

    id: str = Field(default_factory=_short_id)
    session_id: str
    turn_index: int = 0
    role: str  # "founder" | "ceo"
    content: str
    kind: str = "message"
    cost: float = 0.0
    run_id: str | None = None
    directive_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RevenueProposal(BaseModel):
    owner: str = "cro"
    summary: str
    target_amount: float | None = None
    shortfall: float = 0.0
    proposed_paths: list[str] = Field(default_factory=list)


class FinancialEvaluation(BaseModel):
    owner: str = "cfo"
    current_balance: float
    target_amount: float | None = None
    shortfall: float = 0.0
    viable: bool
    rationale: str


class DecisionSynthesis(BaseModel):
    owner: str = "cos"
    consensus: str
    risks: list[str] = Field(default_factory=list)
    recommendation: str


class CEOApprovalPacket(BaseModel):
    owner: str = "ceo"
    approved_direction: str
    rationale: str
    requires_user_approval: bool = True


class COOExecutionPlan(BaseModel):
    owner: str = "coo"
    steps: list[str] = Field(default_factory=list)
    assigned_agents: list[str] = Field(default_factory=list)


class DecisionChainPacket(BaseModel):
    id: str = Field(default_factory=_short_id)
    raw_input: str
    revenue_proposal: RevenueProposal
    financial_evaluation: FinancialEvaluation
    synthesis: DecisionSynthesis
    ceo_approval: CEOApprovalPacket
    execution_plan: COOExecutionPlan
    approval_id: str | None = None
    status: str = "awaiting_approval"


class CLevelReview(BaseModel):
    owner: str
    verdict: str  # "approved" | "needs_revision" | "rejected"
    notes: str = ""


class ExecutionReport(BaseModel):
    project_id: str
    approval_id: str
    packet_id: str
    status: str  # "awaiting_delivery_approval" | "needs_revision"
    tasks_completed: int = 0
    tasks_failed: int = 0
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    reviews: list[CLevelReview] = Field(default_factory=list)
    delivery_approval_id: str | None = None
    total_ai_cost: float = 0.0


class RuntimeState(BaseModel):
    state: str = "running"  # "running" | "suspended"
    reason: str | None = None
    since: datetime | None = None


class NotificationEvent(BaseModel):
    kind: str
    severity: str = "info"
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)


class HeartbeatReport(BaseModel):
    status: str = "ok"
    runtime: dict[str, Any]
    pending_approvals: int = 0
    active_projects: int = 0
    notifications: list[NotificationEvent] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BackupRecord(BaseModel):
    id: str
    label: str = "manual"
    kind: str = "manual"  # "manual" | "auto"
    path: str
    size_bytes: int = 0
    created_at: datetime


class ToolAuthorizationPolicy(BaseModel):
    agent_role: str
    tool_name: str
    allowed: bool = False
    requires_approval: bool = False
    reason: str = ""
    updated_at: datetime | str | None = None


class OutwardActionPolicy(BaseModel):
    """Per-action-CLASS outward pre-authorization policy (ADR-0008 #2).

    ``mode`` is ``'auto'`` (the outward lane executes unattended) or
    ``'gated'`` (parked for ``kompany approve``). Hard floors in
    ``core/outward_policy.py`` can still force ``'gated'`` regardless of
    this stored mode.
    """

    action_class: str
    mode: str = "gated"
    reason: str = ""
    updated_at: datetime | str | None = None


class ToolAuthorizationResult(BaseModel):
    agent_role: str
    tool_name: str
    allowed: bool
    status: str  # "allowed" | "denied" | "approval_required" | "executed" | "failed"
    reason: str = ""
    approval_id: str | None = None
    result: dict[str, Any] | None = None


class RPGCharacter(BaseModel):
    role: str
    room: str
    status: str = "idle"
    current_task: str = ""
    updated_at: str | None = None


class RPGOfficeRoom(BaseModel):
    name: str
    purpose: str
    characters: list[RPGCharacter] = Field(default_factory=list)


class ObservabilitySnapshot(BaseModel):
    status: str = "ok"
    company: dict[str, Any]
    runtime: dict[str, Any]
    finance: dict[str, Any]
    approvals: dict[str, Any]
    projects: dict[str, Any]
    agents: dict[str, Any]
    tools: dict[str, Any]
    notifications: list[NotificationEvent] = Field(default_factory=list)
    office: dict[str, Any]
    # Daemon tick loop (06-12): newest-first autonomous tick rows.
    recent_ticks: list[dict[str, Any]] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Reflection(BaseModel):
    agent_role: str
    content: str


class Retrospective(BaseModel):
    project_id: str
    status: str  # "recorded" | "already_recorded" | "skipped_no_project"
    summary: str = ""
    tasks_completed: int = 0
    tasks_failed: int = 0
    reflections: list[Reflection] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DeliveryPackage(BaseModel):
    approval_id: str
    project_id: str | None = None
    packet_id: str | None = None
    status: str  # "delivered" | "already_delivered" | "needs_revision"
    tasks_completed: int = 0
    tasks_failed: int = 0
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    reviews: list[CLevelReview] = Field(default_factory=list)
    released_at: datetime | None = None
    released_by: str | None = None
    notes: str = ""


class Task(BaseModel):
    id: str = Field(default_factory=_short_id)
    project_id: str
    title: str
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    parent_task_id: str | None = None
    # Harness execution leg (06-11-harness-execution-leg PR4). Per-task
    # caps assigned at decomposition time (PRD D3) and the vehicle session
    # identity persisted after each run so an engine restart can resume
    # the same session (PRD D4 state bridge). All optional + defaulted so
    # pre-PR4 rows and call sites stay legal.
    budget_cap_usd: float | None = None
    max_turns: int | None = None
    harness_session_id: str | None = None
    harness_vehicle: str | None = None


class Decision(BaseModel):
    id: str = Field(default_factory=_short_id)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    directive_id: str
    directive_type: str
    raw_input: str
    classification: dict[str, Any]
    result: dict[str, Any]
    agents_involved: list[str]
    total_ai_cost: float = 0.0
    duration_seconds: float | None = None


class CompanySnapshot(BaseModel):
    """Current state of the company for agent context."""
    name: str
    goal: str
    stage: str
    balance: float
    active_project_count: int
    time_horizon: str = ""
    exclusions: str = ""
    total_revenue: float = 0.0
    total_expenses: float = 0.0
    total_ai_costs: float = 0.0
