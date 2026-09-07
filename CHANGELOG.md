# Changelog

All notable changes to Kompany are documented here.

## [Unreleased]

### Added
- **Self-evolution lane (08-29)** — `<data_dir>/artifacts/` workspace (git-backed souls / workflows / plugin scaffolds) merged last by the plugin loader; `kompany doctor` as the persisted self-test gate (souls, workflows, plugins, ledger, workspace; `doctor/last.json`, `doctor_failed` health event, auto-run on boot and after extension install); skill scopes (`agent` / `company` / `builtin`, per-step `skills:` injection, `kompany skills`); `kompany evolve propose|list|show|revert|status` — one LLM call proposes a soul/workflow YAML, validated, committed, doctor-checked, auto-reverted on failure, privilege changes flagged, daily budget cap. Plugin contract 1.2.0.
- **Workflow inputs contract** — workflow YAML gains an optional top-level `inputs:` list (`name`, `description`, `required`, `source`, `default`, `example`). `WorkflowRunner` validates it; `workflows_list` rows carry `inputs`; a run whose required inputs are missing raises `WorkflowInputsMissing` before any audit row or LLM call.
- **Auto-fill from company state** — `source: company.*` resolves inputs from the ledger (`budget_remaining_usd`, `spend_last_7d_usd`, `revenue_last_7d_usd`), company targets (`revenue_target_usd`, `customer_target`, `deadline`) and company config (`name`, `goal`). `weekly-exec-review` now runs with no inputs on a fresh company. New `Ledger.spent_in_window(days)` / `Ledger.revenue_in_window(days)`.
- **Dry run** — `run_workflow(..., dry_run=True)` returns the resolved inputs and every rendered prompt with zero spend, no audit and no inbox card. Exposed as CLI `kompany workflows run --dry-run`, REST `WorkflowRunRequest.dry_run`, MCP `kompany_workflow_run.dry_run`, SDK `run_workflow(dry_run=True)`.
- **`kompany workflows show <id>`** — display name, description, inputs table, steps table, total estimate and a copy-pasteable example run command (`--json` emits the catalog row).
- Docs: "Running Workflows" section in the usage guide (ten-minute path for the three reference workflows), REST / MCP / SDK tables, README quickstart step 6, `inputs:` block + `source:` table in the plugin contract.

## [2.0.0] - 2026-02-27

### Added
- **Kompany Engine** — Autonomous business operating system with directive-driven architecture
- **16 AI Agents** — 11 C-suite executives + 5 execution subagents (Analyst, Builder, Procurement, Researcher, Writer)
- **Four Interfaces** — CLI (Typer), REST API (FastAPI), MCP Server, Python SDK — all calling the same engine
- **Directive Classification** — CEO auto-classifies into ACQUISITION, STRATEGIC, OPERATIONAL, INFORMATIONAL
- **Mission Integrity** — Budget shortfall creates revenue projects instead of downgrading the mission
- **AI Cost Tracking** — Every LLM call is a real expense in the company ledger
- **Autonomy Tiers** — Auto-execute (€5), CEO-approved (€50), Master-approved (unlimited)
- **Revenue Project Execution** — Subagents autonomously decompose and execute project tasks
- **SQLite Persistence** — Ledger, projects, tasks, decisions, agent memory
- **Agent Soul System** — 11 personality YAML files with per-agent learning across directives
- **Claude Code Skill** — `/kompany` skill for direct invocation
- Comprehensive README with detailed usage guide for all four interfaces
- Star history chart and Buy Me a Coffee support link

### Fixed
- Replaced deprecated `datetime.utcnow()` with `datetime.now(UTC)` across all models

## [1.2.0] - 2026-02-25

### Added
- Squad Architecture (Spotify model) — Strategy, Product, and Growth squads
- Three-File Identity System (SOUL.md / USER.md / MEMORY.md per agent)
- Agent-to-Agent direct communication (intra-squad direct, cross-squad mediated)
- Time-Phased Execution (data agents first, then debate, then CEO review)
- Data Layer pre-round step (CV + CFO gather evidence before debate)
- OpenClaw native deployment support
- Claude Code skill (`.claude/skills/ai-csuite/SKILL.md`)
- Publication docs: README, Usage Guide, OpenClaw Integration Guide

### Changed
- Agent roster now organized by Squad membership, not flat list
- Round 2 rules updated for intra-squad direct communication

## [1.1.0] - 2026-02-24

### Added
- Guardrails and safety system (input/output/tripwire validation)
- Cost management with per-debate hard ceiling ($2 max)
- Solo mode for bootstrapped founders
- Error handling with retry and model fallback chain
- Context window management with summarization strategy
- Three-tier memory system (short-term, entity, long-term)
- Decision journal with outcome tracking
- Human-in-the-loop intervention points
- Observability and tracing (structured spans per agent call)
- Evaluation and testing framework (self-scoring rubric, regression scenarios)
- Structured output validation via Pydantic models

### Changed
- Models updated: Sonnet 4.6 (primary), Opus 4.6 (CEO), Haiku 4 (fallback)
- Primary user refined to "Solo founders and micro-teams (1-5 people)"
- Success metrics expanded with cost per decision, decision quality score

## [1.0.0] - 2026-02-23

### Added
- Initial PRD with 9 C-suite agents + CoS + CV
- Multi-round debate protocol (independent → rebuttal → convergence)
- CEO decision layer with extended thinking
- Stage-based agent selection (solo, pre-seed, seed, series-a)
- JSON debate logging
