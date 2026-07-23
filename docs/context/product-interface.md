# Product & Interface

## product vision
The long-term product is a visual RPG interface where the user is the boss of a virtual company with AI employees earning real money.

**Meaning:** Near-term: visual kanban board for project progress. Long-term: an immersive RPG-like experience where the user walks through their virtual company, sees each agent working, issues directives, and watches real revenue being generated. All workers are virtual; all money is real.

**Implication:** Current architecture must keep agent state, activity status, and interactions structured and queryable enough to eventually render in a spatial UI.

## user interaction
User interaction has three modes, all through existing interfaces, classified automatically by the engine.

**Meaning:** (1) **Directive** — user sets a new goal or task, triggers full decision chain. (2) **Query** — user asks about status, spending, progress; COO or CFO answers directly without triggering decision flow. (3) **Override** — user intervenes to stop, reprioritize, or change direction. On override, the team does not blindly execute. CEO routes it to relevant agents for a focused risk assessment: what this change impacts, what could go wrong, and what trade-offs are involved. The team presents a clear risk briefing to the user. Only after the user acknowledges the risks and confirms does the override take effect.

**Implication:** The team respects the user as supreme decision maker but has the duty to inform. A good team challenges bad decisions with evidence, not obedience. Users may not understand the full consequences of an override — the team's job is to make those consequences visible before execution.

## notification delivery
The engine emits notification events; the interface layer decides how to deliver them.

**Meaning:** Notifications include alerts, approval requests, and completion reports. CLI shows them on the next command, API calls a webhook, MCP pushes to the host agent, SDK triggers a callback.

**Implication:** Adding new notification channels only requires interface-layer changes, not engine changes.

## observability
Users see summary-level information by default, with drill-down access to full detail.

**Meaning:** Four levels of visibility: (1) Dashboard — project status, spending, progress at a glance. (2) Decision — every Journal entry visible (who proposed what, why approved/rejected). (3) Debate — full debate transcripts available on demand, not pushed. (4) Execution — subtask logs queryable on demand. Users are never overwhelmed with detail, but everything is traceable.

**Implication:** All agent activity, decisions, and interactions must be stored in structured form so they can drive both text-based queries now and a visual kanban / RPG-style interface later.

## live activity stream
The engine publishes agent activity over SSE with an advisory `activity_kind` field so visual frontends can render what kind of work is happening.

**Meaning:** Agent status events carry an `activity_kind` derived from the role (coding, marketing, …; `idle` when not working). Harness execution adds `activity_kind: "harness"`: while a task runs as a real CLI session, every normalized session event is republished on the EventHub as a `harness.event` whose payload carries `project_id`, `task_id`, `agent_role`, `kind` (e.g. `session_started`, `turn`, `tool_use`, `text`, `cost_delta`, `permission_denied`), and `summary` (a short human-readable line). The daemon tick loop adds `activity_kind: "tick"`: every autonomous tick publishes a `daemon.tick` event whose payload carries `activity_kind: "tick"`, `outcome` (`ok` / `idle_suspended` / `error`), `actions` (the list of what the tick did, e.g. `heartbeat`, `advanced_task:<id>`, `skipped_pending_approval:<project_id>`, `no_work`), and `duration_ms`.

**Implication:** `activity_kind: "harness"` and `activity_kind: "tick"` are purely additive to the SSE activity contract — existing consumers, including the kompany-world (repo-B) sprite-world UI, keep working unchanged and can opt in to render live harness activity or the daemon's heartbeat pulse.

## remote access
Telegram bot serves as a remote interface adapter for mobile control, alongside the existing local interfaces.

**Meaning:** A Telegram bot is another interface in the same adapter pattern as board Chat, CLI/API/MCP/SDK. Each adapter normalizes transport identity into the same engine-level directive context, so project and agent isolation is not Telegram-specific. The engine may hand a focused conversation from CEO to one eligible specialist and persists that owner across follow-up messages; replies identify the active agent and project. Complex multi-agent requests keep CEO ownership while durable child tasks run in the background. Telegram creates one editable status message with participating agents, phase, elapsed time, cost, and a context-bound Cancel control; milestones edit that message, while completion sends one separate CEO synthesis rather than raw child output. Credential unlock, MFA, re-authentication, replacement, and approval blockers edit the same status message with an HTTPS action and a context-bound Retry control that resumes the original task only after broker preflight succeeds. `/agent`, `/ceo`, `/project`, `/new`, and `/status` provide deterministic manual control. Telegram supports the three user interaction modes: directive (issue commands), query (check status/spending/progress), and override (intervene with risk assessment). AutonomyGate approval requests can be pushed as Telegram messages, and the user can approve/reject directly from their phone. Other messaging platforms (Discord, Slack, WeChat) can be added as additional adapters later.

**Implication:** Remote adapters do not own routing or context policy; they authenticate and normalize input, while the shared engine enforces isolation and routing. Security is critical: Telegram bot must authenticate the user and encrypt sensitive data.

## mobile support
Mobile access follows three phases from lightweight to full experience.

**Meaning:** Phase 1 (now): Telegram bot as zero-cost mobile entry point for directive/query/override and AutonomyGate approvals. Phase 2 (post-validation): PWA with visual kanban, project status, and financial dashboard, driven by existing REST API. Phase 3 (later stage): native app with full RPG interface experience. Each phase validates what users actually need on mobile before investing in the next.

**Implication:** Do not build a native app before validating mobile usage patterns through Telegram and PWA. Mobile is an interface concern — engine and decision chain remain unchanged.
