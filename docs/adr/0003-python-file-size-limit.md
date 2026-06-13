# ADR-0003: Python source files ≤ 500 lines

**Status:** Accepted (2026-05-25)
**Deciders:** Founder (solo)

## Context

Kompany is AI-coded end to end. Every new feature, fix, and refactor
goes through an LLM agent (Claude / Codex / Cursor / OpenClaw / etc.)
reading existing files and emitting diffs. The size of those files
directly determines how reliable the agent's edits are.

`core/engine.py` had grown to **4482 lines** as of commit 28e6c7f
without an explicit size rule constraining it. The recent
custom-provider routing bug (commits 197ccf3 → 3eadfc0 → 28e6c7f)
required four iterations precisely because the agent kept missing
related code paths that lived hundreds or thousands of lines apart
in the same file.

## Decision

Adopt a hard **500-line cap** for Python source files under
`kompany/src/`. Above that:

- Extract a cohesive concern into a sibling module, OR
- Convert into a package directory with `__init__.py` re-exporting
  the public surface, OR
- For a class that has grown organically, introduce a mixin module
  that the class inherits from (minimal call-site churn —
  preferred for `KompanyEngine`).

The rule is enforced by reviewer judgment + the spec entry in
`the internal design spec` (a hard rule visible at
every task start via `before-dev` skill).

## Why 500 and not 1000 / 1500 / 2000

Three industry anchors converge near 500:

- Robert C. Martin (*Clean Code*) recommends a soft target around
  500 for class files.
- Linux kernel C convention: functions ≤ 50 lines; files typically
  ≤ 1000.
- Python community well-maintained libraries (`requests`, `click`,
  `typer`, `pytest`) — > 90 % of modules are under 500.

The decisive factor is AI-agent reliability. Empirical observation
(this project, this session, multiple commits):

| File length    | Agent reliability                              |
|----------------|------------------------------------------------|
| < 500 lines    | High; near-zero hallucination                  |
| 500 - 1500     | Good; occasional missed details                |
| 1500 - 3000    | Notable: misplaced edits, repeated implementations |
| > 3000         | Bad: invents methods, wrong line offsets, fakes signatures |

1500 was considered as a compromise. Rejected because:
- It targets the "agent already struggling" band, not the safe band.
- The split work scales linearly either way; deferring just moves
  the cost.
- Setting a high cap signals tolerance for sprawl.

## Consequences

**Positive:**
- AI-agent edits across long sessions stay accurate. Fewer
  iterations to hit a bug fix (the vault-key + routing bug took 4
  commits because related code lived 4000 lines apart).
- New contributors (post-OSS-launch) can read a single file end to
  end without losing the thread.
- Smaller test files map 1:1 to smaller modules.
- Pre-launch repo doesn't show 4000-line "Python God Class" to first
  HN/Reddit visitors.

**Negative:**
- One-time refactor cost for over-limit existing files (engine.py,
  cli.py, possibly api.py). Estimated 1-3 working sessions for the
  engine split.
- Slightly more import statements at call sites.
- Method resolution order (MRO) bookkeeping if mixins proliferate.
  Cap mixin count per class at 3; revisit if hit.

**One-way gates:**
- None. The rule is reversible by deleting the convention entry, and
  the splits themselves can be reverted via `git revert`.

## Alternatives considered

1. **No rule, rely on judgment.** Status quo until this ADR. Fails
   because nobody (human or AI) intervenes proactively until a
   specific bug surfaces.
2. **Soft 800-line warning.** Common in linter configs. Rejected
   because warnings are routinely ignored when feature pressure is on.
3. **Hard 1000-line cap.** Compromise position. Rejected per the
   "Why 500" reasoning above — sits in the agent-degraded band.
4. **Hard 1500-line cap.** Same problem, more so.
5. **Per-class limit instead of per-file.** Misses the case where
   one file contains many small classes (e.g. multiple Pydantic models).
   Per-file is the unit the LLM agent actually reads.

## Sample split — commit reference

The same commit that lands this ADR extracts
`run_target_feasibility_review` + its helpers from `engine.py` into
`core/target_review.py` as a mixin (`TargetReviewMixin`).
`KompanyEngine(... TargetReviewMixin)` inherits the methods; no
call-site changes required.

Lines moved: ~870.
Engine.py post-split: ~3600 lines (still over-limit; further splits
queued in the OSS-launch backlog).

## Re-eval triggers

- 3rd consecutive PR splits a different over-limit file → consider
  introducing a `ruff` plugin or CI check that fails on > 500.
- Mixin count for `KompanyEngine` exceeds 3 → revisit whether a
  proper service-decomposition refactor is overdue (the mixin pattern
  is a transitional aid, not the destination).
- AI agent demonstrably handles 1500-line files reliably in a future
  model generation → consider relaxing to 800.

## References

- Liu et al. 2023, *Lost in the Middle: How Language Models Use
  Long Contexts* — https://arxiv.org/abs/2307.03172
- Robert C. Martin, *Clean Code* — Chapter 1 (vertical formatting).
- `the internal design spec` (hard rule entry).
- `.agents/memory/engineering-file-size-limit.md`.
