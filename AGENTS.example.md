# Kompany — Development Agent Instructions (example)

Instructions for AI coding agents (Claude Code, Cursor, Codex, Aider, …)
working in this repository. Copy this file to `AGENTS.md` (gitignored)
and adapt it to your own setup.

For installed/runtime behavior of the Kompany product itself, see
`KOMPANY.md`.

## Core rules

- Follow `CONTEXT.md` and `docs/context/*.md` as the source of truth for
  design decisions. Architecture decision records live in `docs/adr/`.
- Use Python 3.11+ with type hints and Pydantic v2 for models and settings.
- Set up the dev environment per [CONTRIBUTING.md](CONTRIBUTING.md):
  `cd kompany && python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[api,mcp,dev]"`
- Run `python -m pytest tests/ -v` before committing.
- Keep Python files ≤ 500 lines (see `docs/adr/0003-python-file-size-limit.md`).
- Do not run `git push` unless asked.
- Do not create documentation, changelog, or summary files unless
  explicitly requested.

## Response style

- When the direction is clear, proceed directly instead of asking for
  confirmation.
- Report what changed, why it changed, and what comes next.
- Only ask when a major direction change or real ambiguity would change
  the work.

## Licensing note for contributions

Kompany Core is AGPL-3.0 and dual-licensed; external contributions
require a one-time CLA (the bot prompts on your first PR). See
[CONTRIBUTING.md](CONTRIBUTING.md) and [docs/why-agpl.md](docs/why-agpl.md).
