# Episode Payload Schema (v1.0)

Frozen contract for the `project_episodes.payload_json` column produced by
the CoS retrospective and consumed by every downstream learning surface
(distillation, crystallization, the four-surface `episodes get` command,
future approval-thread RPG, future resilience watchdog).

> **Authoritative model**: import from
> `kompany.state.episode_payload.EpisodePayloadV1`. The schema is locked by
> [`05-18-episode-schema-freeze`](../../.trellis/tasks/05-18-episode-schema-freeze/prd.md).
> Adding a new top-level key is a **breaking change** and requires bumping
> `schema_version`. Forward-compatible extensions live under `ext`
> namespaced by task slug.

## Why a wide schema today

`05-18-self-learning-episodes` materializes the payload from six existing
tables. Three known-but-unimplemented consumers will also need to ride
inside this payload later:

1. **Approval thread RPG** (paperclip P0 #2) — player counter-proposals and
   revision requests on approval requests.
2. **Resilience watchdog** (collab report P1 #3 #4) — `stranded_todo`,
   `stranded_in_progress`, `silent_run` task states and watchdog alerts.
3. **Run-id full-chain tracing** ([`05-18-run-id-tracing`](../../kompany/src/kompany/core/run_context.py))
   — every event carries the `run_id` of the directive that produced it.

Freezing all three slots now keeps future work non-breaking: old readers
ignore empty `approval_events` / `health_events` lists; new readers fill
them in without a schema-version bump.

## Top-level keys

The payload is a JSON object with exactly the following top-level keys.
Unknown top-level keys are **rejected** (`model_config = ConfigDict(extra="forbid")`) —
forward-compatible additions go under `ext` (see below).

| Key | Type | Status | Who fills | Who reads | Default |
|---|---|---|---|---|---|
| `schema_version` | `"1.0"` literal | locked | episode materializer | all readers (version-gate) | `"1.0"` |
| `run_ids` | `list[str]` (run-id pattern) | live | episode materializer (aggregated from `audit_log.run_id`) | distillation P1, trace tooling | `[]` |
| `project_meta` | `ProjectMeta` | live | episode materializer | all readers | required |
| `tasks` | `list[TaskEntry]` | live | episode materializer (from `tasks` table) | distillation P1 | `[]` |
| `ledger_summary` | `LedgerSummary` | live | episode materializer (aggregated from `ledger`) | distillation P1 | empty `LedgerSummary` |
| `decisions` | `list[DecisionEntry]` | live | episode materializer (from `decisions` table) | distillation P1 | `[]` |
| `debate_ids` | `list[str]` | live | episode materializer (from `debates` table) | distillation P1 | `[]` |
| `audit_events` | `list[AuditEvent]` | live | episode materializer (key rows from `audit_log`) | trace + distillation P1 | `[]` |
| `reflections` | `list[ReflectionEntry]` | live | from `agent_memories` where `category='reflection'` | distillation P1 | `[]` |
| `approval_events` | `list[ApprovalEvent]` | **reserved** | future `approval-thread-and-rpg` (after `approval_comments` subtable lands) | distillation P1 (player preference patterns) | `[]` |
| `health_events` | `list[HealthEvent]` | **reserved** | future `resilience-foundation` (after watchdog event table lands) | distillation P1 (fragility patterns) | `[]` |
| `ext` | `dict[str, Any]` | permanent extension point | any future task, namespaced by task slug | compatibility container | `{}` |

### Nested fields

| Field | Notes |
|---|---|
| `tasks[].run_id` | `str \| None`, pattern `^r_[0-9A-HJKMNP-TV-Z]{26}$` — matches `kompany.core.run_context.RUN_ID_PATTERN`. |
| `tasks[].lifecycle_events[].state` | Free-form string. Today: `todo \| in_progress \| blocked \| done`. Reserved values from `resilience-foundation`: `stranded_todo \| stranded_in_progress \| silent_run`. |
| `decisions[].run_id` | Same pattern. `None` for historical rows. |
| `audit_events[].run_id` | Same pattern. `None` outside a `run_scope`. |
| `approval_events[].run_id` | Same pattern. |
| `health_events[].run_id` | Same pattern. |

## Run-id format

All `run_id` fields use the same regex as
`kompany.core.run_context.RUN_ID_PATTERN`:

```
^r_[0-9A-HJKMNP-TV-Z]{26}$
```

This is `r_` followed by a 26-character Crockford base32 ULID (no `I`,
`L`, `O`, `U`). `new_run_id()` always produces a value that matches.
`None` is permitted because writers outside a `run_scope` (CLI bootstrap,
backup scripts, ad-hoc test setup) leave the column `NULL`.

## Extension rules (`ext`)

`ext` is the **only** place to add new data without bumping
`schema_version`. Rules:

- Keys MUST be namespaced by task slug, e.g.
  `ext["approval-thread-and-rpg"]`, `ext["resilience-foundation"]`.
- Values are arbitrary JSON-serialisable objects.
- Readers MUST ignore unknown `ext` keys (forward compatibility).
- Once a feature lands a first-class top-level slot (as `approval_events`
  and `health_events` did pre-emptively here), the corresponding `ext`
  entry should be deleted in a single migration task and `schema_version`
  bumped if shape changes were necessary.

## Reserved-slot semantics

`approval_events` and `health_events` ship as `[]` today. Both downstream
consumers (distillation P1, crystallization P2) MUST treat empty lists as
"feature not yet active" and not error out. The list type is **never**
`None` — always a list, possibly empty.

## Minimal example

A just-delivered project with no approvals, no watchdog activity, no
decisions or audit events worth surfacing:

```json
{
  "schema_version": "1.0",
  "run_ids": [],
  "project_meta": {
    "id": "proj_a1b2",
    "name": "Bootstrap landing page",
    "mission": null,
    "target_funded": [],
    "status": "delivered",
    "created_at": "2026-05-18T09:00:00Z",
    "delivered_at": "2026-05-18T11:30:00Z"
  },
  "tasks": [],
  "ledger_summary": {
    "total_income": 0.0,
    "total_expense": 0.0,
    "ai_cost": 0.0,
    "by_category": {},
    "by_agent": {}
  },
  "decisions": [],
  "debate_ids": [],
  "audit_events": [],
  "reflections": [],
  "approval_events": [],
  "health_events": [],
  "ext": {}
}
```

## Full example

A delivered project that exercises every slot, including the reserved
`approval_events` / `health_events` slots and an `ext` namespace entry:

```json
{
  "schema_version": "1.0",
  "run_ids": [
    "r_01HXX0000000000000000000AB",
    "r_01HXY0000000000000000000CD"
  ],
  "project_meta": {
    "id": "proj_a1b2",
    "name": "Ship invoice export",
    "mission": "Let solo founders bill their first client in <5 minutes.",
    "target_funded": [500.0, 500.0],
    "status": "delivered",
    "created_at": "2026-05-10T09:00:00Z",
    "delivered_at": "2026-05-18T18:42:00Z"
  },
  "tasks": [
    {
      "id": "task_001",
      "title": "Design PDF template",
      "assigned_agent": "cto",
      "status": "completed",
      "result": "shipped templates/invoice_v1.pdf",
      "run_id": "r_01HXX0000000000000000000AB",
      "lifecycle_events": [
        {"at": "2026-05-10T09:05:00Z", "state": "todo", "reason": null},
        {"at": "2026-05-10T09:30:00Z", "state": "in_progress", "reason": null},
        {"at": "2026-05-10T14:00:00Z", "state": "done", "reason": null}
      ]
    },
    {
      "id": "task_002",
      "title": "Wire export endpoint",
      "assigned_agent": "cto",
      "status": "completed",
      "result": "POST /invoices/{id}/export.pdf",
      "run_id": "r_01HXY0000000000000000000CD",
      "lifecycle_events": [
        {"at": "2026-05-15T10:00:00Z", "state": "todo", "reason": null},
        {"at": "2026-05-15T11:00:00Z", "state": "in_progress", "reason": null},
        {"at": "2026-05-16T09:00:00Z", "state": "stranded_in_progress",
         "reason": "watchdog: no tool calls in 18h"},
        {"at": "2026-05-17T10:00:00Z", "state": "in_progress", "reason": "watchdog recovered"},
        {"at": "2026-05-17T16:00:00Z", "state": "done", "reason": null}
      ]
    }
  ],
  "ledger_summary": {
    "total_income": 500.0,
    "total_expense": 12.40,
    "ai_cost": 9.85,
    "by_category": {"income": 500.0, "ai_cost": 9.85, "expense": 2.55},
    "by_agent": {"cto": 7.20, "cos": 1.40, "ceo": 1.25}
  },
  "decisions": [
    {
      "id": "dec_001",
      "directive_id": "dir_abc",
      "run_id": "r_01HXX0000000000000000000AB",
      "summary": "Use ReportLab over wkhtmltopdf — no system deps.",
      "agents_involved": ["ceo", "cto"]
    }
  ],
  "debate_ids": ["debate_001"],
  "audit_events": [
    {
      "at": "2026-05-10T09:00:00Z",
      "type": "project.created",
      "run_id": "r_01HXX0000000000000000000AB",
      "detail": {"project_id": "proj_a1b2"}
    },
    {
      "at": "2026-05-18T18:42:00Z",
      "type": "project.delivered",
      "run_id": "r_01HXY0000000000000000000CD",
      "detail": {"funded": 500.0}
    }
  ],
  "reflections": [
    {
      "agent_role": "cto",
      "category": "reflection",
      "content": "wkhtmltopdf install kept failing on CI — ReportLab was the right call."
    },
    {
      "agent_role": "cos",
      "category": "reflection",
      "content": "Watchdog catch saved ~1 day of wasted re-run."
    }
  ],
  "approval_events": [
    {
      "id": "appr_001",
      "run_id": "r_01HXY0000000000000000000CD",
      "kind": "expense_over_threshold",
      "outcome": "revision_requested",
      "comments": [
        {"by": "user", "at": "2026-05-15T20:00:00Z",
         "text": "$50 looks high — can we use a cheaper model for draft?"},
        {"by": "cto", "at": "2026-05-15T20:05:00Z",
         "text": "Agreed. Switching draft pass to Haiku, final pass stays Sonnet."},
        {"by": "user", "at": "2026-05-15T20:08:00Z", "text": "ok approved."}
      ],
      "decided_at": "2026-05-15T20:08:00Z"
    }
  ],
  "health_events": [
    {
      "at": "2026-05-16T09:00:00Z",
      "run_id": "r_01HXY0000000000000000000CD",
      "kind": "stranded_in_progress",
      "task_id": "task_002",
      "detail": {"silent_for_seconds": 64800}
    },
    {
      "at": "2026-05-17T10:00:00Z",
      "run_id": "r_01HXY0000000000000000000CD",
      "kind": "recovered",
      "task_id": "task_002",
      "detail": {"action": "re-prompted cto"}
    }
  ],
  "ext": {
    "approval-thread-and-rpg": {
      "player_xp_delta": 12,
      "player_preference_tags": ["cost-sensitive", "explicit-model-choice"]
    }
  }
}
```

## Versioning policy

- `schema_version` is a literal string. v1 ships as `"1.0"`.
- Adding a new **reserved** top-level slot requires bumping to `"1.1"` (or
  later) — old readers should still parse old payloads, so the model must
  retain backward-compat default values.
- Changing the **shape** of an existing slot (renaming a field, changing
  type) is a major bump (`"2.0"`) and requires an `episodes rebuild --all`
  pass to migrate.
- The `ext` dict is **never** versioned by `schema_version`. It is an
  unversioned forward-compat container — namespace conflicts are resolved
  by the task that owns the namespace.
