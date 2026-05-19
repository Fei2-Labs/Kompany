"""Cross-episode distillation — P1 of the self-learning roadmap.

The Chief of Staff (CoS) reviews the most recent N project episodes and
synthesises a small set of high-density *experiential* patterns: stable
observations that any future agent should consult when handling similar
work. Each pattern is anchored to one ``target_agent_role`` (the agent
expected to consume it) and carries a short ``pattern_key`` used for
idempotent UPSERT into ``agent_memories``.

This module contains the **Pydantic schemas** and the **pure helper
functions** the engine wires together. ``KompanyEngine.distill`` owns the
``run_scope`` wrapping, audit-event emission, and DB writes; everything
here is side-effect-free beyond the single LLM call.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kompany.state.episode_payload import EpisodePayloadV1


# ---------------------------------------------------------------------------
# Knobs
# ---------------------------------------------------------------------------

# Default "how far back to look" when ``--since`` is not supplied. Matches
# the PRD's "default 30d" decision.
DEFAULT_SINCE = timedelta(days=30)

# Hard ceiling on the number of episodes a single distillation call may
# consume. Above this, callers must use ``--episodes`` to narrow the
# selection. Keeps the prompt under ~50 KB even with verbose payloads.
MAX_EPISODES_PER_RUN = 50

# Per-episode summary character budget so 50 episodes × ~1.6 KB ≈ 80 KB
# total context, comfortably inside both Claude and GPT-4o windows.
PER_EPISODE_CHAR_BUDGET = 1800

# Known agent roles a distilled pattern may target. Patterns naming an
# unknown role are dropped with a warning rather than crashing.
KNOWN_AGENT_ROLES: frozenset[str] = frozenset({
    "ceo", "cfo", "cto", "cpo", "cmo", "cro",
    "coo", "csa", "ciso", "cos", "cv",
})


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class DistilledPattern(BaseModel):
    """One cross-episode pattern produced by CoS distillation."""

    model_config = ConfigDict(extra="forbid")

    target_agent_role: str = Field(
        description="Agent role expected to consult this memory "
        "(ceo/cfo/cto/cpo/cmo/cro/coo/csa/ciso/cos/cv).",
    )
    pattern_key: str = Field(
        min_length=1,
        max_length=40,
        description="Stable short key (max 40 chars, lowercase-with-hyphens) "
        "used for idempotent UPSERT. The LLM must reuse the same key "
        "when describing the same underlying pattern.",
    )
    pattern_summary: str = Field(
        min_length=1,
        description="One paragraph human-readable description of the pattern.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Self-reported confidence, 0.0 (low) to 1.0 (high).",
    )
    evidence_episode_ids: list[str] = Field(
        default_factory=list,
        description="Project ids of the episodes that support this pattern.",
    )

    @field_validator("target_agent_role")
    @classmethod
    def _lower_role(cls, value: str) -> str:
        # We don't reject unknown roles at validation time — the engine
        # filters them post-validation so we can emit a warning event
        # rather than failing the whole distillation.
        return value.strip().lower()

    @field_validator("pattern_key")
    @classmethod
    def _normalize_pattern_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        # ``min_length=1`` is enforced *before* the validator runs, so a
        # whitespace-only key would slip through and collapse to "" here.
        # Re-check post-normalization so the UPSERT contract holds.
        if not normalized:
            raise ValueError("pattern_key must not be blank after normalization")
        return normalized


class DistillationOutput(BaseModel):
    """The structured object the CoS LLM call must produce."""

    model_config = ConfigDict(extra="forbid")

    patterns: list[DistilledPattern] = Field(default_factory=list)
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form bookkeeping (episodes_consumed, "
        "total_input_chars, etc.). Not validated.",
    )


class EpisodeTooManyError(ValueError):
    """Raised when more than :data:`MAX_EPISODES_PER_RUN` episodes are picked."""


# ---------------------------------------------------------------------------
# Episode selection + summarization
# ---------------------------------------------------------------------------

def _parse_episode_ts(raw: str | None) -> datetime | None:
    """Parse the SQLite ``updated_at`` string into a UTC datetime."""
    if not raw:
        return None
    text = raw.strip()
    # SQLite ``datetime('now')`` returns ``YYYY-MM-DD HH:MM:SS`` without a
    # zone marker. ``datetime.fromisoformat`` accepts both forms once we
    # turn the space into a 'T'.
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def select_episode_rows(
    rows: list[dict[str, Any]],
    *,
    episode_ids: list[str] | None,
    since: timedelta | None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Pick the rows to feed into a distillation run.

    The selection rules mirror the PRD:

    * Explicit ``episode_ids`` wins — only those rows are kept (in the
      order requested). Missing ids are silently dropped; the engine
      reports the resolved subset back to the caller.
    * Otherwise the caller-supplied ``since`` (defaulting to 30 days)
      filters by ``updated_at``. ``since=0`` and future dates return an
      empty list.
    * Rows without a ``payload_json`` (trimmed to summary) are skipped
      because distillation needs the full structured payload.

    Returns the filtered rows in newest-first order.
    """
    full_rows = [r for r in rows if r.get("payload_json")]

    if episode_ids:
        wanted = list(dict.fromkeys(episode_ids))  # preserve order, dedupe
        index = {r["project_id"]: r for r in full_rows}
        return [index[pid] for pid in wanted if pid in index]

    cutoff = None
    if since is not None:
        # ``since=0`` means "look back zero seconds" — produces an empty
        # window regardless of clock. Negative offsets are clamped to now.
        seconds = max(0.0, since.total_seconds())
        anchor = now or datetime.now(timezone.utc)
        cutoff = anchor - timedelta(seconds=seconds)

    selected: list[dict[str, Any]] = []
    for row in full_rows:
        if cutoff is not None:
            ts = _parse_episode_ts(row.get("updated_at"))
            if ts is None or ts < cutoff:
                continue
        selected.append(row)
    return selected


def summarize_episode(payload: EpisodePayloadV1) -> dict[str, Any]:
    """Build a compact dict summary of one episode for the LLM prompt.

    The shape mirrors what the CoS prompt asks for: project meta + a
    list of reflections + the approval outcomes + health-event kinds +
    a ledger summary. We deliberately drop full audit-event bodies and
    raw approval comments beyond a short snippet so the per-episode
    chunk fits inside :data:`PER_EPISODE_CHAR_BUDGET`.
    """
    meta = payload.project_meta

    reflections = [
        {"agent_role": r.agent_role, "content": r.content[:400]}
        for r in payload.reflections
    ]

    approval_summary: list[dict[str, Any]] = []
    for ae in payload.approval_events:
        first_comment = ae.comments[0].text[:200] if ae.comments else ""
        approval_summary.append({
            "kind": ae.kind,
            "outcome": ae.outcome,
            "comment_count": len(ae.comments),
            "first_comment": first_comment,
        })

    # Health events: aggregate by ``kind`` so the prompt sees the
    # frequency of e.g. ``silent_run`` vs ``stranded_in_progress``
    # without exhaustively listing every row.
    health_by_kind: dict[str, int] = {}
    for he in payload.health_events:
        health_by_kind[he.kind] = health_by_kind.get(he.kind, 0) + 1

    ledger = {
        "total_income": payload.ledger_summary.total_income,
        "total_expense": payload.ledger_summary.total_expense,
        "ai_cost": payload.ledger_summary.ai_cost,
    }

    task_status_counts: dict[str, int] = {}
    for t in payload.tasks:
        task_status_counts[t.status] = task_status_counts.get(t.status, 0) + 1

    return {
        "project_id": meta.id,
        "project_name": meta.name,
        "status": meta.status,
        "mission": (meta.mission or "")[:200],
        "task_status_counts": task_status_counts,
        "reflections": reflections,
        "approval_events": approval_summary,
        "health_events_by_kind": health_by_kind,
        "ledger_summary": ledger,
        "decisions": [
            {"id": d.id, "summary": d.summary[:200]}
            for d in payload.decisions
        ],
    }


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3] + "..."


def build_episode_summaries(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse each row's payload and produce per-episode summary dicts.

    Returns ``(summaries, parse_failures)``. ``parse_failures`` carries the
    project ids of rows whose ``payload_json`` did not validate against
    :class:`EpisodePayloadV1` so the engine can surface the count via the
    audit event without polluting the LLM prompt.
    """
    summaries: list[dict[str, Any]] = []
    failures: list[str] = []
    for row in rows:
        raw = row.get("payload_json")
        if not raw:
            failures.append(row.get("project_id") or "<unknown>")
            continue
        try:
            payload = EpisodePayloadV1.model_validate_json(raw)
        except Exception:  # noqa: BLE001 — bad payload should not abort run
            failures.append(row.get("project_id") or "<unknown>")
            continue
        summary = summarize_episode(payload)
        summaries.append(summary)
    return summaries, failures


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

DISTILLATION_SYSTEM_PROMPT = (
    "You are the Chief of Staff (CoS) performing cross-episode distillation. "
    "You review project retrospectives — reflections, approval outcomes, "
    "health events, ledger figures — and identify *durable cross-project "
    "patterns* the company's agents should remember.\n\n"
    "Rules:\n"
    "1. Output ONLY JSON matching the schema described in the user prompt.\n"
    "2. Each pattern must target ONE agent role from: ceo, cfo, cto, cpo, "
    "cmo, cro, coo, csa, ciso, cos, cv. Pick the role most likely to "
    "consult the memory next time.\n"
    "3. ``pattern_key`` must be a short stable id (lowercase-with-hyphens, "
    "max 40 chars). Reuse the same key on future runs if you see the same "
    "pattern again — that is how memories stay deduplicated.\n"
    "4. ``confidence`` is your honest 0.0–1.0 estimate of how reliably the "
    "pattern will recur.\n"
    "5. ``evidence_episode_ids`` lists the project ids that support the "
    "pattern. Only cite ids you saw in the input.\n"
    "6. Prefer 3–8 high-signal patterns over a long thin list.\n"
    "7. If the input contains no useful pattern, return an empty patterns "
    "list — do NOT invent patterns."
)


def build_distillation_user_prompt(summaries: list[dict[str, Any]]) -> str:
    """Assemble the user-side prompt body from per-episode summaries."""
    parts: list[str] = [
        "Review the following project episodes and extract durable patterns.",
        f"Total episodes: {len(summaries)}.",
        "",
        "EPISODES:",
    ]
    for idx, summary in enumerate(summaries, start=1):
        block = json.dumps(summary, indent=2, ensure_ascii=False)
        block = _truncate(block, PER_EPISODE_CHAR_BUDGET)
        parts.append(f"--- Episode {idx} ({summary['project_id']}) ---")
        parts.append(block)
    parts.append("")
    parts.append(
        "Respond with a JSON object matching the DistillationOutput "
        "schema (patterns: list, meta: object). Patterns should generalize "
        "across episodes — avoid one-off observations."
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pure post-processing
# ---------------------------------------------------------------------------

def filter_patterns(
    output: DistillationOutput,
) -> tuple[list[DistilledPattern], list[dict[str, Any]]]:
    """Apply the post-validation filters.

    * Patterns whose ``target_agent_role`` is unknown are dropped with a
      warning record (returned in the second tuple element).
    * Same-batch ``(agent_role, pattern_key)`` collisions resolve to the
      *last* occurrence (matches the PRD: "Same pattern_key from same
      agent in single batch → keep last").
    """
    warnings: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], DistilledPattern] = {}
    order: list[tuple[str, str]] = []
    for pattern in output.patterns:
        role = pattern.target_agent_role
        if role not in KNOWN_AGENT_ROLES:
            warnings.append({
                "reason": "unknown_target_agent_role",
                "pattern_key": pattern.pattern_key,
                "target_agent_role": role,
            })
            continue
        key = (role, pattern.pattern_key)
        if key not in by_key:
            order.append(key)
        else:
            warnings.append({
                "reason": "duplicate_pattern_key_in_batch",
                "pattern_key": pattern.pattern_key,
                "target_agent_role": role,
            })
        by_key[key] = pattern
    return [by_key[k] for k in order], warnings


__all__ = [
    "DEFAULT_SINCE",
    "DISTILLATION_SYSTEM_PROMPT",
    "DistillationOutput",
    "DistilledPattern",
    "EpisodeTooManyError",
    "KNOWN_AGENT_ROLES",
    "MAX_EPISODES_PER_RUN",
    "PER_EPISODE_CHAR_BUDGET",
    "build_distillation_user_prompt",
    "build_episode_summaries",
    "filter_patterns",
    "select_episode_rows",
    "summarize_episode",
]
