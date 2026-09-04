# Context

Kompany design decisions, organized by domain. Each link leads to the full definitions.

**Sync rule:** These documents are the source of truth for system design. When implementation diverges from what's documented here, either update the code to match or update the docs to reflect the new decision — never leave them out of sync.

## [Core Architecture](docs/context/core-architecture.md)
Agent system identity, orchestration model, agent communication, debate termination, context management, language strategy.

## [Agent Roles](docs/context/agent-roles.md)
All 11 C-level roles (CEO, CFO, COO, CTO, CPO, CMO, CRO, CoS, CISO, CSA, CV), 5 execution subagents, role boundaries, lifecycle, activity status, turnover, soul governance, soul design methodology, team dynamics.

## [Decision & Governance](docs/context/decision-governance.md)
Decision chain, AutonomyGate, autonomy tiers, decision journal, constitution, safety guardrails, tool authorization, tool registry, quality assurance, compliance/security review, feasibility assessment.

## [Self-Learning & Knowledge](docs/context/self-learning.md)
Self-learning architecture (Observe → Reflect → Distill → Govern), knowledge lifecycle, knowledge validity, domain knowledge, skill crystallization, cold start, new-domain evaluation.

## [Operations](docs/context/operations.md)
Budget policy, financial monitoring, multi-project concurrency, multi-business operation, project relationships, post-completion flow, procurement scope, external service integration, initialization flow (5 inputs: name, capital, goal, time horizon, exclusions), stage upgrade.

## [Infrastructure](docs/context/infrastructure.md)
LLM routing, LLM cost model (pay-per-token / subscription), data persistence (SQLite), audit log, resource exhaustion (checkpoint-and-resume), error handling (retry → degrade → escalate), heartbeat, deployment, multi-tenancy, backup.

## [Product & Interface](docs/context/product-interface.md)
Product vision (RPG interface), user interaction (directive / query / override), notification delivery, observability (4-level drill-down), remote access (Telegram bot), mobile support (3-phase: Telegram → PWA → native).

## [Execution Rules](docs/context/execution-rules.md)
Runtime invariants for agent routing, cost tracking, time handling, and governed behavior changes.

## Open-Core Model
AGPL-3.0 (dual-licensed) boundary: Core (engine + plugin contract) is open; Pro (workflow library + agent souls + integrations) is a separate private package; Cloud is a future SaaS. Rationale: [`docs/why-agpl.md`](docs/why-agpl.md) and the README "Open Core" section.

## [Plugin Contract](docs/context/plugin-contract.md)
Stable Core↔Pro surface at `kompany.plugins.*` (v1.1.0). Five ABCs: Tool / AgentSoul / Integration / Workflow / Template (+ OutwardExecutor). Discovery via Python entry points; compat via pip dep pinning. 1.1.0 adds optional `ToolContext` service stores (documents / artifacts / approvals / journal / events), `Workflow.bind(engine)`, and `engine.register_approval_effect`. See [ADR-0002](docs/adr/0002-plugin-contract-design.md).

## [Branding Department](docs/context/branding-department.md)
Brand strategy + gated visual brand-system as a Workflow/Tool plugin (no new permanent roles): CMO owns strategy/voice, CV owns visuals/Brand Lock, CEO recommends, founder approves via the existing inbox. Brand Memory = versioned documents in Core's generic `ProjectDocumentStore`; approved versions immutable; artifacts declare JSON-path dependencies and go stale on upstream change. Slice 1 (`brand-foundation`) shipped; visual/campaign/audit slices pending.
