# Approval thread + RPG inbox

Built by `05-18-approval-thread-and-rpg`. Replaces the flat
`pending / approved / rejected` flow with a full decision thread so the
player can counter-propose, snooze, cancel, or comment — and so
distillation (later `05-17-self-learning-evolution` P1) can learn from
the player's revision pattern.

## State machine

```
                +-----------+
                | revision_ |
                | requested |  (terminal)
                +-----^-----+
                      |
   +-----------+      |        +-----------+
   |  pending  +------+        | cancelled |  (terminal)
   +-----+---+-^------+        +-----^-----+
         |   | |                     |
   approve|  | |request_revision     |cancel
         v   | |                     |
   +---------+ |                     |
   | approved| |snooze         +-----+-----+
   |(terminal)| |              | snoozed   |
   +---------+ |               | (auto-    |
               |               | unsnoozes |
               |               |   via     |
               |               |  watchdog)|
               |               +-----+-----+
   +---------+ |                     |
   | rejected|<+---------------------+
   |(terminal)|        all 5 non-self
   +---------+         transitions legal
```

**Terminal states** (no outgoing transitions): `approved`, `rejected`,
`revision_requested`, `cancelled`.

**Non-terminal**: `pending`, `snoozed`. The watchdog scanner (`Watchdog.
_scan_snoozed_approvals` running every `scan_interval_seconds`)
auto-transitions snoozed -> pending once `snoozed_until <= datetime('now')`.

**Idempotency**: re-approving an already-approved row (or re-rejecting an
already-rejected one) is a silent no-op — needed for the remote command
replay path. All other terminal -> any transitions raise
`IllegalApprovalTransition`.

## Schema

`approval_requests` adds four columns (additive migration):

| column           | type | default     | notes                          |
| ---------------- | ---- | ----------- | ------------------------------ |
| `severity`       | TEXT | `'medium'`  | `info|low|medium|high|critical`|
| `predecessor_id` | TEXT | `NULL`      | self-reference, revision chain |
| `snoozed_until`  | TEXT | `NULL`      | SQLite datetime, UTC           |
| `snoozed_by`     | TEXT | `NULL`      | who snoozed (player/agent)     |

New table `approval_comments`:

```
id            TEXT PRIMARY KEY
approval_id   TEXT NOT NULL    -- no FK constraint enforced (SQLite default)
by_type       TEXT NOT NULL    -- 'user' | 'agent' | 'system'
by_id         TEXT             -- agent role, player alias, or NULL
body          TEXT NOT NULL
created_at    TEXT             -- datetime('now') default

INDEX idx_approval_comments_approval_id ON approval_comments(approval_id)
INDEX idx_approval_requests_status      ON approval_requests(status)
INDEX idx_approval_requests_predecessor_id ON approval_requests(predecessor_id)
```

`created_at` is second-resolution; ties tie-break on `rowid ASC` so the
rendered order matches insertion order even within a single second.

## Revision handler registry

When a player calls `request_revision` on an approval, the original is
flipped to `revision_requested` (terminal) + the counter-proposal is
written as a comment. The engine then dispatches to a per-`action_type`
revision handler that produces the successor approval:

```python
engine.register_revision_handler(
    action_type,
    handler: Callable[[ApprovalRequest, str], ApprovalRequest],
)
```

The handler must persist + return a fresh approval with
`predecessor_id = original.id` so `list_thread` can link them.

**Default fallback** (`KompanyEngine._default_revision_handler`):
copies the original `payload`, stamps the counter text into
`payload['revision_hint']`, inherits `severity`, and submits the new row
as `pending`. This is intentionally simple — it does not re-run any LLM
generation path. Each caller will register a proper LLM-driven handler
in a follow-up task.

**Anti-loop guarantee**: the default handler does not itself call
`request_revision`. A second revise on the successor creates a third
approval (predecessor chain of three rows) — never an unbounded loop.

## Four-surface UX

The same 8 actions are reachable from every surface:

| Action   | CLI                              | SDK                                    | REST                                   | MCP                            |
|----------|----------------------------------|----------------------------------------|----------------------------------------|--------------------------------|
| inbox    | `kompany inbox`                  | `Kompany.inbox()`                      | `GET /inbox`                           | `kompany_inbox`                |
| show     | `kompany approval show <id>`     | `Kompany.approvals_ns.show(id)`        | `GET /approvals/<id>`                  | `kompany_approval_show`        |
| approve  | `kompany approval approve <id>`  | `Kompany.approvals_ns.approve(id)`     | `POST /approvals/<id>/approve`         | `kompany_approval_approve`     |
| reject   | `kompany approval reject <id>`   | `Kompany.approvals_ns.reject(id, …)`   | `POST /approvals/<id>/reject`          | `kompany_approval_reject`      |
| revise   | `kompany approval revise <id>`   | `Kompany.approvals_ns.revise(id, …)`   | `POST /approvals/<id>/revise`          | `kompany_approval_revise`      |
| snooze   | `kompany approval snooze <id>`   | `Kompany.approvals_ns.snooze(id, …)`   | `POST /approvals/<id>/snooze`          | `kompany_approval_snooze`      |
| cancel   | `kompany approval cancel <id>`   | `Kompany.approvals_ns.cancel(id, …)`   | `POST /approvals/<id>/cancel`          | `kompany_approval_cancel`      |
| comment  | `kompany approval comment <id>`  | `Kompany.approvals_ns.comment(id, …)`  | `POST /approvals/<id>/comment`         | `kompany_approval_comment`     |

The legacy CLI commands `kompany approvals` / `kompany approve` /
`kompany reject` remain available as the original three-state subset.

## Episode payload integration

`Episodes._collect_approval_events(project_id)` joins
`approval_requests` + `approval_comments` and materialises one
`ApprovalEvent` per approval row into
`EpisodePayloadV1.approval_events`. Each `ApprovalEvent` carries:

- `id`, `run_id`, `kind` (= `action_type`), `outcome` (= `status`)
- `comments`: ordered list of `ApprovalComment` (`by`, `at`, `text`)
- `decided_at`: ISO timestamp of the terminal transition

Distillation reads this slot to learn player preference patterns (e.g.
"player revises 8/10 CFO budget proposals to half").
