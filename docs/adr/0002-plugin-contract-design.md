# ADR-0002: Plugin contract — entry points, 5 ABCs, thick Tool

**Status:** Accepted (2026-05-22)
**Deciders:** Founder (solo), via grill-with-docs session (8 questions, all confirmed)
**Implements:** Decision recorded in [ADR-0001](0001-open-core-with-plugin-contract.md) ("open-core via plugin contract").

## Context

ADR-0001 chose open-core via plugin contract. This ADR pins the contract's shape. Authoritative reference: [`docs/context/plugin-contract.md`](../context/plugin-contract.md). Implementation: `kompany/src/kompany/plugins/`.

## Decisions

Eight decisions, each with rejected alternatives:

### 1. Discovery = Python entry points

Plugins register via `[project.entry-points."kompany.*"]` in their `pyproject.toml`; Core scans via `importlib.metadata` at engine init.

**Rejected: filesystem directory scan.** Would block Pro from shipping real API clients (Stripe SDK, Polar webhooks) — kills the Integration ABC's purpose.

**Rejected: single `kompany_pro` package with `register(engine)`.** Allows only one Pro install at a time; can't mix `kompany-pro-saas` + `kompany-pro-ecom`.

### 2. Five plugin kinds, not three

`Workflow / AgentSoul / Integration / Template / Tool`.

**Rejected: three (Workflow / AgentSoul / Integration).** Templates are already a Core concept (`kompany/src/kompany/templates/community/` reserved as extension hook) — omitting Template from the contract leaves an obvious gap. Tools are the agent-facing surface; without a Tool ABC, every Integration reinvents agent-binding.

**Why "many ABCs cheap, few ABCs expensive":** ABCs are the one piece of code where adding later breaks every published plugin. Pay the up-front cost.

### 3. Compat enforcement = pip dep pinning

Plugins declare `kompany>=X.Y,<X.(Y+1)` in `pyproject.toml`; pip blocks Core upgrades that violate the range.

**Rejected: no version check (silent breakage).** Wrong for "Pro plugins must not silently break."

**Rejected: runtime version check + Pro auto-disable on mismatch.** Will revisit if community plugins emerge with lagging update cadence. For solo-Pro stage, pip pinning is simpler and louder.

### 4. Tool = thick (declares cost + autonomy + side-effect)

Tools declare `side_effect`, `autonomy_tier`, `estimate_cost()` — engine wraps invocation with cost ledger, AutonomyGate, audit log.

**Rejected: thin Tool.** Would let Pro add a `stripe.create_invoice` Tool that spends real money without flowing through Kompany's cost-visibility / AutonomyGate machinery. Breaks Kompany's "real money, real audit" identity (memories: [[engineering-cost-visibility-discipline]] + [[engineering-evidence-traced-debate]]).

### 5. Integration = pure tool source + credentials

Integration declares `required_credentials` and exposes `tools()`. No passive state, no scheduled tasks.

**Rejected: Integration with passive state provider** (auto-injected into agent context per turn). Defer — additive minor bump if ever needed. MVP: agents call Tools when they want data.

**Rejected: Integration with scheduled tasks.** Requires a scheduler component Core does not have. Defer.

### 6. AgentSoul = YAML default via `SoulAgent`, Python subclass escape

Most Pro souls = YAML file describing persona / tone / allowed tools / debate behavior, run by Core's `SoulAgent` class. Override `run()` only for behavior YAML cannot express. Pro souls **add** roles — never replace Core's 11 C-suite + 5 subagents.

**Rejected: YAML-only.** No escape hatch for specialist behavior (custom debate scoring, etc.).

**Rejected: YAML + mandatory Python class.** Forces 30+ min boilerplate for souls that only need persona changes (80% case).

**Naming:** chose `SoulAgent` over `GenericAgent` (collision with existing `lsdefine/GenericAgent` repo) and over `RoleAgent` (confused with Core's role concept). `Soul` is already canonical in glossary.

### 7. Workflow = hybrid YAML + Python escape

Top-level steps declarative (YAML) so engine can compute cost preview, AutonomyGate, audit BEFORE running. Individual steps may point to a Python callable.

**Rejected: imperative Python `def run(ctx)`.** Engine sees a black box — cost PREVIEW becomes impossible. Violates cost-visibility discipline.

**Rejected: pure declarative YAML.** Schema can't express complex branching ("skip onboarding if customer already paid").

### 8. Template = extend Core `manifest.json`, reference workflows/souls by ID

Pro Templates add three fields to existing Core schema: `bundled_workflow_ids`, `enabled_pro_soul_ids`, `required_integration_ids`. Workflows / souls referenced, not embedded.

**Rejected: independent `ProTemplate` format.** Forces two onboarding template lists (Core vs Pro). Same UX problem solved by additive fields on one schema.

**Rejected: embed workflows inside Template manifest.** Prevents one Workflow being referenced by multiple Templates (e.g. `weekly-exec-review` shared across SaaS / Ecom / Content Creator Pro Templates).

## Consequences

**Positive:**
- Pro authors get a small, predictable surface (5 ABCs, ~200 LoC of contract).
- Engine governance (cost, AutonomyGate, audit, evidence-traced debate) applies uniformly to Pro and Core without Pro authors writing plumbing.
- One Pro plugin pack works alongside any number of others (`kompany-pro-saas` + `kompany-pro-ecom`).
- Pip-level version pinning surfaces incompatibility loudly at install time, not at runtime.
- Single onboarding template list — Pro Templates appear next to Core ones with a badge.

**Negative:**
- Core gains ~3 new runtime classes that don't exist yet: `SoulAgent`, `WorkflowRunner`, plus extension of `Templates` to discover Pro templates via entry points. Each is its own follow-up.
- The contract executes Pro Python code in Core's process. Trust boundary deferred — acceptable while Pro = founder-authored. Revisit before opening community plugin acceptance.
- `python_callable` workflow steps are opaque to cost preview — authors must hand-fill `cost_estimate_usd` for those steps.
- Pip pinning blocks Core upgrades when Pro lags. Tolerable while Pro versions in lockstep with Core; revisit at community-plugin stage.

**One-way gates:**
- `__contract_version__ = "1.0.0"` is publicly committed in `kompany.plugins`. Bumping the major is breaking — must be done deliberately and not before at least 3 Pro plugins exist (to gauge real upgrade pain).

## Alternatives considered (whole-design level)

Beyond per-decision alternatives above:

**Skip plugin contract entirely; bundle Pro into Core's main repo with feature flags.** Rejected because Core's repo is public on GitHub since inception — anything committed to Core is forever public, defeating Pro's commercial scope.

**Use an existing plugin framework (e.g. `pluggy`).** Rejected because Kompany's governance hooks (cost preview, AutonomyGate, evidence-traced debate) are project-specific; an off-the-shelf plugin framework would still need wrapping. Stdlib `importlib.metadata` is enough and removes a dependency.

## Re-eval triggers

- 3rd Pro plugin author needs an ABC the contract doesn't have → MINOR bump, add ABC.
- 3rd cost-preview-blind workflow step shows up → consider tightening the `python_callable` escape (e.g. mandatory cost field).
- Community plugin acceptance opens → revisit decisions 1 (sandbox?), 3 (runtime version check?), 7 (constrain Python escape further?).

## References

- Contract module: `kompany/src/kompany/plugins/` (`__init__.py`, `contract.py`, `loader.py`)
- Tests: `kompany/tests/test_plugins_contract.py` (14 cases, freezes public surface)
- Reference docs: [`docs/context/plugin-contract.md`](../context/plugin-contract.md)
- Boundary parent: [ADR-0001](0001-open-core-with-plugin-contract.md), [`open-core-model.md`](../context/open-core-model.md)
- Glossary: Kompany Core, Kompany Pro, plugin contract, SoulAgent
- Decision substrate: grill-with-docs session 2026-05-22 (8 questions, all locked)
