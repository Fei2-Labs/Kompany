# Self-Learning & Knowledge

## self-learning architecture
Reuse keel-volvo's design patterns (episode logging, reflection, crystallization, distillation) but adapt to Kompany's multi-agent + SQLite architecture.

**Meaning:** Do not copy keel-volvo code directly. Reuse its Observe → Reflect → Distill → Govern philosophy and adapt the storage to the existing agent_memories table and ProjectRunner callbacks.

**Implication:** Episode logging and reflection should land first (low risk). Crystallization and distillation come later once the system is stable. CoS should own the retrospective process.

## knowledge lifecycle
Self-learning data follows a three-layer retention policy triggered periodically by CoS.

**Meaning:** Episode logs keep full detail for the most recent N projects (default 10, CTO adjusts), older ones are summarized. Reflections are permanent but merged during distillation when redundant. Distilled knowledge is permanent and the system's most valuable long-term asset. More abstract knowledge lives longer; raw data is compressed early — like human memory retaining lessons but forgetting specific dates.

**Implication:** Storage pressure is managed by CTO. CoS owns the distillation schedule.

## knowledge validity
Knowledge is split into experiential (lessons, strategies, patterns) and factual (prices, rates, platform rules, APIs), with different lifecycle rules.

**Meaning:** Experiential knowledge is cumulative and ages gracefully. Factual knowledge carries `recorded_at` and `valid_until` timestamps; expired entries are marked `stale`, not deleted, so historical trends remain available. Updates happen passively (agent discovers discrepancy during execution) and actively (COO dispatches researcher to re-verify critical facts before project milestones).

**Implication:** Agents must be able to judge the value, validity, and timeliness of their own knowledge. This meta-cognitive ability — knowing what you know, what's outdated, and what needs re-verification — is a core self-learning capability, not an add-on.

## domain knowledge
Agent domain knowledge is acquired dynamically through three channels, never by modifying soul files.

**Meaning:** (1) Researcher subagent conducts industry research and stores structured reports in agent_memories. (2) Self-learning accumulates experience from past project execution. (3) Users provide domain knowledge through AutonomyGate interactions or by specifying knowledge sources (websites, books, documents, etc.) that researcher should study. All domain knowledge is stored in agent_memories and clearly surfaced to the user so they know where it lives and can review, correct, or supplement it.

**Implication:** Soul files define how agents think, not what they know. Knowledge is dynamic and project-scoped; personality is stable and system-scoped. Users can point the team at specific sources to accelerate domain learning.

## skill crystallization
The system automatically generates reusable skills and SOPs from repeated patterns and past mistakes.

**Meaning:** When self-learning detects that the team has solved a similar problem multiple times, or that a mistake keeps recurring, CoS triggers crystallization: the pattern is extracted into a reusable skill or SOP document (similar to keel-volvo's crystallize process). These skills become part of the team's operational playbook — agents reference them in future tasks to avoid repeating errors and to apply proven approaches.

**Implication:** The system writes its own best practices over time. Skills are living documents that evolve through the self-learning loop, not static templates.

## cold start
On first run, agents rely on LLM pre-trained knowledge and real-time research, not system experience.

**Meaning:** The self-learning system starts empty. Agents use LLM general knowledge for initial reasoning, and researcher conducts live research for specific facts (prices, platform rules, market conditions). First-round results will be less refined than later ones — this is expected. No seed knowledge or training data is pre-loaded.

**Implication:** Self-learning value compounds over time. The system gets better with every project executed.

## new-domain evaluation
When a goal is achieved through a method in a completely new domain, the team must evaluate whether to continue in that domain.

**Meaning:** CoS leads a retrospective to determine if success was driven by replicable capability or a one-time opportunity. If replicable, CRO + CPO propose a new ongoing-type project through the standard decision chain. If one-time, close the goal-type project and preserve experience in the self-learning system.

**Implication:** Entering a new domain is always a strategic decision requiring user approval. The team should not blindly expand into unfamiliar territory.

## P0 implementation — episode logging
At every project delivery, `engine.run_retrospective` writes a single structured record into `project_episodes.payload_json` (built from the frozen `EpisodePayloadV1` schema in `kompany.state.episode_payload`). The payload aggregates the project's metadata, tasks, ledger summary, decisions, `debate_ids`, curated audit events, and reflections — so downstream consumers (distillation, crystallization, future replay tooling) read **one row** instead of joining six tables.

Strategic debates persist in their own `debates` table the moment `_handle_strategic_debate` finishes the multi-round protocol; the resulting `debate_id` flows into `decisions.result` and is referenced from the episode payload (never embedded — debates can be reused across directives).

**Retention** is governed by `company_config['episode_retention_full_count']` (default 50). After every retrospective, episodes beyond the window are demoted to `retention_tier='summary'`: detailed `payload_json` is cleared, but the one-line `summary` and `debate_ids` remain. The six source tables stay untouched, so a trimmed project can be re-materialized at any time via `episodes rebuild <project_id>` (CLI / SDK / API / MCP).

**Audit events emitted**: `learning.episode_recorded`, `learning.episode_trimmed`, `debate.recorded`.

**Resilience signal — `health_events` slot**: `05-18-resilience-foundation` populates the `EpisodePayloadV1.health_events` slot at materialization time by JOINing the new `health_events` table on `project_id`. Each entry carries the watchdog `kind` (`silent_run`, `recovered`, `retry_exhausted`, `stranded_in_progress`, `stranded_todo`), the founder resolution (`status` ∈ `open|resolved|snoozed|dismissed`, plus `resolved_by` / `resolved_at` / `snoozed_until`), and the originating `run_id`. P1 distillation uses this slot to learn cross-project fragility patterns — e.g. "Tuesday-afternoon 429 spikes on provider X" — without re-scanning the source table.
