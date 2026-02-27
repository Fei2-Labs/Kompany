# Changelog

All notable changes to Kompany are documented here.

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
