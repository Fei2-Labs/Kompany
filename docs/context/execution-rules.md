# Execution Rules

## datetime handling
Use `datetime.now(UTC)` instead of `datetime.utcnow()`.

**Meaning:** Use timezone-aware UTC timestamps for all runtime and persistence code.

**Implication:** Avoid naive datetimes and deprecated UTC helpers.

## cost tracking
Track costs per agent call via `CostTracker`.

**Meaning:** Every LLM or agent call must be accounted for as a real operating expense.

**Implication:** Cost tracking is part of system behavior, not optional logging.

## engine routing
All interfaces must call `KompanyEngine`.

**Meaning:** CLI, REST, MCP, and SDK are adapters around the same core engine.

**Implication:** Do not bypass the engine from any public interface.

## soul governance
Soul files (`souls/*.yaml`) require governed changes; do not edit them directly.

**Meaning:** Soul behavior changes follow review and approval, not ad hoc edits.

**Implication:** Treat soul files as behavioral configuration with system-wide impact.
