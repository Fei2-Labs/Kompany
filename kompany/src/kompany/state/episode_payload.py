"""Frozen schema for ``project_episodes.payload_json``.

This module defines :class:`EpisodePayloadV1`, the Pydantic v2 contract that
:mod:`05-18-self-learning-episodes` must use when materialising a project's
episode JSON payload, and that every downstream reader (distillation,
crystallization, the four-surface ``episodes get`` command, future
approval-thread and resilience tooling) must read against.

The schema is intentionally **wider than today's implementation**: it carves
out reserved slots (``approval_events``, ``health_events``, ``run_ids``,
``ext``) for known-but-unimplemented consumers so that adding those features
later is a **non-breaking** change — old readers simply see empty lists / an
empty dict for unknown-yet slots.

See ``docs/context/episode-payload-schema.md`` for the human-readable
contract, the "who fills / who reads / default value" matrix, and full
worked examples.

Run-id format note
------------------
``run_id`` fields are typed ``str | None`` with the pattern
``^r_[0-9A-HJKMNP-TV-Z]{26}$``. This matches
:data:`kompany.core.run_context.RUN_ID_PATTERN` exactly — values produced by
:func:`kompany.core.run_context.new_run_id` always validate. ``None`` is
permitted because not every historical row carries a run id (CLI bootstrap,
backups, ad-hoc test writes leave the column ``NULL``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Kept in sync with ``kompany.core.run_context.RUN_ID_PATTERN``. Duplicated
# here as a literal (rather than imported) so the schema file is
# self-contained for downstream consumers that only depend on the state
# package.
RUN_ID_PATTERN = r"^r_[0-9A-HJKMNP-TV-Z]{26}$"


class _Strict(BaseModel):
    """Base for nested models — forbid unknown keys to enforce schema discipline."""

    model_config = ConfigDict(extra="forbid")


class ProjectMeta(_Strict):
    """Project-level metadata snapshotted at retrospective time."""

    id: str
    name: str
    mission: str | None = None
    target_funded: list[float] = Field(default_factory=list)
    status: str
    created_at: str
    delivered_at: str | None = None


class LifecycleEvent(_Strict):
    """One state transition on a task.

    ``state`` is a free-form string today but future ``resilience-foundation``
    work will write ``stranded_todo`` / ``stranded_in_progress`` /
    ``silent_run`` here — readers must tolerate any value.
    """

    at: str
    state: str
    reason: str | None = None


class TaskEntry(_Strict):
    """One task row inside the episode."""

    id: str
    title: str
    assigned_agent: str | None = None
    status: str
    result: str | None = None
    run_id: str | None = Field(default=None, pattern=RUN_ID_PATTERN)
    lifecycle_events: list[LifecycleEvent] = Field(default_factory=list)


class LedgerSummary(_Strict):
    """Aggregated cost / income figures for the project."""

    total_income: float = 0.0
    total_expense: float = 0.0
    ai_cost: float = 0.0
    by_category: dict[str, float] = Field(default_factory=dict)
    by_agent: dict[str, float] = Field(default_factory=dict)


class DecisionEntry(_Strict):
    """One CEO/CoS decision record bound to the project."""

    id: str
    directive_id: str | None = None
    run_id: str | None = Field(default=None, pattern=RUN_ID_PATTERN)
    summary: str
    agents_involved: list[str] = Field(default_factory=list)


class AuditEvent(_Strict):
    """One key audit-log row materialised into the episode."""

    at: str
    type: str
    run_id: str | None = Field(default=None, pattern=RUN_ID_PATTERN)
    detail: dict[str, Any] = Field(default_factory=dict)


class ReflectionEntry(_Strict):
    """One agent reflection sourced from ``agent_memories``."""

    agent_role: str
    category: str = "reflection"
    content: str


class ApprovalComment(_Strict):
    """One comment inside an approval thread.

    Reserved for the future ``approval-thread-and-rpg`` task — the schema
    accepts these now so distillation can pattern-match player preference
    once that feature lands.
    """

    by: str
    at: str
    text: str


class ApprovalEvent(_Strict):
    """One approval decision (with its comment thread).

    Reserved slot: today's materializer emits an empty list. Filled in by
    the future ``approval-thread-and-rpg`` task.
    """

    id: str
    run_id: str | None = Field(default=None, pattern=RUN_ID_PATTERN)
    kind: str
    outcome: str
    comments: list[ApprovalComment] = Field(default_factory=list)
    decided_at: str | None = None


class HealthEvent(_Strict):
    """One watchdog / resilience event.

    Filled by the ``05-18-resilience-foundation`` task. ``id`` / ``status`` /
    ``project_id`` / ``resolved_by`` / ``resolved_at`` / ``snoozed_until``
    are additive: pre-resilience payloads simply omit them and the defaults
    apply. ``schema_version`` stays ``"1.0"`` because no existing field
    changed meaning.

    ``kind`` is one of ``stranded_todo`` / ``stranded_in_progress`` /
    ``silent_run`` / ``recovered`` / ``retry_exhausted``; ``status`` is
    ``open`` / ``resolved`` / ``snoozed`` / ``dismissed``. Both are typed
    as ``str`` so future kinds can land without breaking parsers — readers
    that switch on the value must treat unknown values as inert.
    """

    at: str
    run_id: str | None = Field(default=None, pattern=RUN_ID_PATTERN)
    kind: str
    task_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    # New optional fields added by resilience foundation.
    id: str | None = None
    project_id: str | None = None
    status: str = "open"
    resolved_by: str | None = None
    resolved_at: str | None = None
    snoozed_until: str | None = None


class TargetsSnapshot(_Strict):
    """One captured ``CompanyTargets`` state inside an episode payload.

    Mirrors :class:`kompany.state.targets.CompanyTargets` but stays
    declared locally so the episode schema doesn't import from
    higher-level packages. All four numeric fields default to safe
    "no target set" values; older payloads that pre-date mission-targets
    (task 05-19) simply omit the parent ``targets`` slot entirely.
    """

    initial_budget: float = 0.0
    revenue_target: float = 0.0
    customer_target: int | None = None
    deadline: str | None = None
    source: str = "founder"


class GlossaryDriftEntry(_Strict):
    """One drift hit captured during the CoS retrospective scan.

    Mirrors :class:`kompany.agents.cos_glossary_scan.DriftHit` but stays
    declared locally so the episode schema doesn't import from
    higher-level packages. The list lives in the top-level
    ``glossary_drift`` slot (additive, defaults to ``None`` for older
    payloads); distillation later pattern-matches "founder pushed back
    on X repeatedly" by reading this slot.
    """

    term: str
    drifted_synonym: str
    agent_role: str
    count: int = 0
    sample_excerpt: str = ""
    source: str = "reflection"


class TargetsBundleEntry(_Strict):
    """Three-state targets snapshot persisted into the episode payload.

    Lets distillation see the full negotiation: what the founder set,
    what the team recommended, and what the founder ultimately agreed
    to. Filled in by ``Episodes.materialize`` from
    :func:`kompany.state.targets.get_bundle`. All four sub-fields are
    optional so projects that pre-date mission-targets (task 05-19) can
    still materialise (the slot will simply be ``None`` at the top
    level).
    """

    founder: TargetsSnapshot | None = None
    proposal: TargetsSnapshot | None = None
    agreed: TargetsSnapshot | None = None
    review_thread_id: str | None = None


class EpisodePayloadV1(BaseModel):
    """Frozen v1 contract for ``project_episodes.payload_json``.

    All 12 top-level keys are part of the stable contract. Reserved slots
    (``approval_events``, ``health_events``, ``run_ids``, ``ext``) default to
    empty collections — readers must tolerate emptiness. Unknown top-level
    keys are **rejected** (``extra="forbid"``) to enforce schema discipline;
    forward-compatible extensions live under ``ext`` namespaced by task slug
    (e.g. ``ext["approval-thread-and-rpg"] = {...}``).

    ``targets`` is **additive** — added by mission-targets (task 05-19).
    ``schema_version`` stays ``"1.0"`` because no existing field changed
    meaning; readers that don't know about ``targets`` simply ignore the
    key, and payloads materialised before the field existed default it
    to ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    run_ids: list[str] = Field(
        default_factory=list,
        description="All run_ids that touched this project, aggregated from audit_log.",
    )
    project_meta: ProjectMeta
    tasks: list[TaskEntry] = Field(default_factory=list)
    ledger_summary: LedgerSummary = Field(default_factory=LedgerSummary)
    decisions: list[DecisionEntry] = Field(default_factory=list)
    debate_ids: list[str] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)
    reflections: list[ReflectionEntry] = Field(default_factory=list)
    approval_events: list[ApprovalEvent] = Field(default_factory=list)
    health_events: list[HealthEvent] = Field(default_factory=list)
    targets: TargetsBundleEntry | None = Field(
        default=None,
        description=(
            "Three-state company targets snapshot (founder / team_proposal / "
            "agreed) + the review approval thread id. Filled in by "
            "Episodes.materialize via kompany.state.targets.get_bundle. "
            "Added by mission-targets task 05-19."
        ),
    )
    glossary_drift: list[GlossaryDriftEntry] | None = Field(
        default=None,
        description=(
            "Forbidden-synonym hits the CoS retrospective scanner found "
            "this episode. ``None`` means scan didn't run (empty "
            "glossary, older payload); ``[]`` means scan ran but found "
            "nothing. Added by glossary-and-drift-detection task 05-19."
        ),
    )
    ext: dict[str, Any] = Field(
        default_factory=dict,
        description="Namespace-by-task-slug escape hatch for forward-compatible additions.",
    )


__all__ = [
    "RUN_ID_PATTERN",
    "ApprovalComment",
    "ApprovalEvent",
    "AuditEvent",
    "DecisionEntry",
    "EpisodePayloadV1",
    "GlossaryDriftEntry",
    "HealthEvent",
    "LedgerSummary",
    "LifecycleEvent",
    "ProjectMeta",
    "ReflectionEntry",
    "TargetsBundleEntry",
    "TargetsSnapshot",
    "TaskEntry",
]
