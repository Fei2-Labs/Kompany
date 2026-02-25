# Changelog

All notable changes to the AI C-Suite Framework are documented here.

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
