# Plugin Contract

Status: v1.0.0 (decided 2026-05-22). See [ADR-0002](../adr/0002-plugin-contract-design.md) for trade-off record.

The plugin contract is the stable Core↔Pro integration surface, defined in `kompany.plugins.*`. Pro / community packages register contributions via Python entry points and pin a Core version range in their `pyproject.toml`.

## Discovery: Python entry points

Plugin packages declare contributions in their `pyproject.toml`:

```toml
[project]
dependencies = ["kompany>=0.2,<0.3"]   # pin compat Core range

[project.entry-points."kompany.workflows"]
14-day-saas-launch = "my_pro_pack.workflows:fourteen_day_launch"

[project.entry-points."kompany.souls"]
saas-compliance-officer = "my_pro_pack.souls:compliance_officer"

[project.entry-points."kompany.integrations"]
stripe = "my_pro_pack.integrations.stripe:integration"

[project.entry-points."kompany.templates"]
saas-pro-starter = "my_pro_pack.templates:saas_pro_starter"

[project.entry-points."kompany.tools"]
stripe.create_invoice = "my_pro_pack.tools.stripe:create_invoice"
```

Core discovers all installed plugins via `kompany.plugins.loader.discover()` on engine init. One-shot — restart Core to pick up newly installed plugins. No hot-reload (MVP; revisit for Cloud).

Failures in a single plugin are caught and surfaced in `discover()["_errors"]` so one broken third-party wheel does not block the rest.

## Five plugin kinds

| ABC | Purpose | Shape |
|---|---|---|
| `Tool` | A callable action (e.g. `stripe.create_invoice`) | Thick: declares `side_effect` + `autonomy_tier` + `estimate_cost` so the engine wires cost ledger, AutonomyGate, audit automatically |
| `AgentSoul` | A new role (12th C-level / 6th subagent / etc.) | YAML default via `SoulAgent`; Python subclass escape hatch. Pro souls **add** new roles — never replace Core's 11 + 5 |
| `Integration` | Connector to an external service (Stripe, Polar, Notion) | Pure tool source + credentials. Declares required creds; exposes Tools |
| `Workflow` | Multi-step business recipe (e.g. `14-day-saas-launch`) | Hybrid: top-level YAML steps for engine introspection (cost preview, AutonomyGate); Python escape for complex steps |
| `Template` | Pre-bundled company starter (e.g. `saas-pro-starter`) | Extends Core's `manifest.json` schema; references workflows / souls / integrations by ID (not embedded) |

## Versioning

`kompany.plugins.__contract_version__` follows semver:

- **MAJOR** bump = breaking change (renamed/removed symbol, changed signature). Plugin authors must update their pin.
- **MINOR** bump = additive (new field with a default, new ABC, new SideEffect enum value). Plugins continue to work.
- **PATCH** bump = doc-only.

Plugin authors pin against the major in `pyproject.toml`: `kompany>=0.2,<0.3`. When Core's contract major bumps, `pip install -U kompany` is blocked until the Pro plugin publishes a compatible release. Loud, intentional, at install time — not silent runtime breakage.

## Thick Tool contract (cost + governance)

Every Tool declares:

| Field | Type | Purpose |
|---|---|---|
| `name` | str | Dotted id, e.g. `"stripe.create_invoice"` |
| `description` | str | LLM-facing, drives agent's tool-selection decision |
| `input_schema` | Pydantic model | Validated before `execute` |
| `output_schema` | Pydantic model | Validated after `execute` |
| `side_effect` | `SideEffect` enum (`read` / `write_local` / `external_action` / `spend`) | Determines ledger + audit behavior |
| `autonomy_tier` | `AutonomyTier` enum (`auto` / `approval` / `human_only`) | Engine routes through AutonomyGate accordingly |
| `estimate_cost(inputs)` | → `CostEstimate(llm_usd, external_usd, confidence)` | Pre-execution projection — enables cost PREVIEW before the Tool runs |
| `execute(inputs, ctx)` | → output | Engine has already applied AutonomyGate by the time this runs |

The engine wraps every Tool invocation with: cost preview emit → AutonomyGate check → execute → ledger row → audit event → cost STREAM emit. Tool authors do not write any of that plumbing.

## AgentSoul YAML schema (sketch)

```yaml
role: saas_compliance_officer       # must be unique across all installed plugins
display_name: SaaS Compliance Officer
tier: c_level                       # or "subagent"
squad: governance                   # c_level only
model_tier: primary
personality:
  tone: cautious, precise
  decision_style: risk-averse, evidence-led
  priorities:
    - PCI scope minimization
    - SOC2 audit readiness
debate_behavior:
  participates: true
  challenges: [cto, cpo]            # which Core roles this soul may challenge
allowed_tools:
  - stripe.*                        # glob over Tool names
  - audit.*
  - "!*.spend"                      # negative glob: never call spend tools
```

The engine's `SoulAgent` runtime reads this YAML, builds the system prompt, restricts tool calls per `allowed_tools`, and participates in debate per `debate_behavior`. Override `AgentSoul.run` only if YAML cannot express the behavior.

## Workflow YAML schema (sketch)

```yaml
workflow_id: 14-day-saas-launch
display_name: 14-day SaaS Launch
steps:
  - id: day1_define_icp
    agent_role: cpo
    tool_calls: [notion.create_page]
    inputs_from: company_state
    cost_estimate_usd: 0.30          # LLM-side; engine sums these
    autonomy_tier: auto
  - id: day3_landing_page
    agent_role: cto
    python_callable: ship_landing    # escape hatch — points to function in plugin
    autonomy_tier: approval
  - id: day7_stripe_setup
    agent_role: cfo
    tool_calls: [stripe.create_product, stripe.create_price]
    autonomy_tier: approval          # any "spend"-class tool forces this
```

Engine computes `estimate_cost()` as sum-of-step + applies AutonomyGate per step before invoking. Steps with `python_callable` are opaque to cost preview — workflow author must populate `cost_estimate_usd` manually for those.

## Template extension (over Core `manifest.json`)

Pro Template manifests use the existing Core schema plus three new fields:

```json
{
  "id": "saas-pro-starter",
  "name": "SaaS Pro Starter",
  ...                                  // all Core fields unchanged
  "bundled_workflow_ids": ["14-day-saas-launch", "weekly-trial-conversion-review"],
  "enabled_pro_soul_ids": ["saas_compliance_officer"],
  "required_integration_ids": ["stripe", "notion"]
}
```

The engine's `Templates` service learns to discover Pro templates via the entry-point loader (in addition to packaged Core templates) and apply them by:
1. Writing standard Core company_config fields
2. Activating the listed Pro souls + workflows
3. Prompting the operator to fill credentials for required integrations
4. Recording a `template.applied` audit event

## What's NOT in 1.0.0

Deferred (additive bumps when needed):
- Hot-reload — restart required
- Integration `context_provider` hook (passive state visible to agents per turn)
- Integration `scheduled_task` hook (cron-style background sync)
- Plugin marketplace / signature verification
- Multi-tenant Cloud sandbox semantics

## Cross-references

- ABCs: `kompany/src/kompany/plugins/contract.py`
- Loader: `kompany/src/kompany/plugins/loader.py`
- Tests: `kompany/tests/test_plugins_contract.py`
- Decision: [ADR-0002](../adr/0002-plugin-contract-design.md)
- Boundary: [`open-core-model.md`](open-core-model.md)
