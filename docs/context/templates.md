# Company templates

Templates are ready-to-play company presets. They let a new founder skip
the empty-canvas problem on day 1: pick a scenario, get a mission, a
starter team, an initial budget, and a few draft directives staged in
the inbox.

## Where templates live

Each template is a directory under `kompany/src/kompany/templates/<id>/`:

```
templates/
  saas-startup/
    manifest.json
    mission.md
    suggested_directives.md          # optional
  indie-tool/
  consulting-firm/
  content-creator/
  ecommerce/
  blank/
  community/                         # optional, scanned at runtime
    <your-template-id>/
      manifest.json
      mission.md
```

Templates ship with the PyPI wheel via the `[tool.hatch.build.targets.wheel]`
`include` block in `pyproject.toml`. The service reads them with
`importlib.resources` so the same code works for editable installs,
wheel installs, and zipped distributions.

## Manifest schema

`manifest.json` must satisfy `kompany.state.templates_model.CompanyTemplate`:

| Field                    | Type             | Required | Notes                                                                    |
|--------------------------|------------------|----------|--------------------------------------------------------------------------|
| `id`                     | string           | yes      | Stable id; matches the directory name.                                   |
| `name`                   | string           | yes      | Human-readable name.                                                     |
| `mission_title`          | string           | yes      | One-line mission statement (used by `kompany template list`).            |
| `mission_md_path`        | string           | yes      | Relative path to the mission markdown body (usually `mission.md`).       |
| `initial_budget`         | number           | yes      | Starting capital in USD; must be `>= 0`.                                 |
| `enabled_agents`         | list[string]     | no       | Subset of `ceo, cfo, cto, cpo, cmo, cro, coo, csa, ciso, cos, cv`.       |
| `agent_config_overrides` | object           | no       | Free-form per-agent tuning (CFO thresholds, agent tone hints, etc).      |
| `suggested_directives`   | list[string]     | no       | Becomes one `status='draft'` project per entry.                          |
| `rpg_theme`              | string           | no       | Theme id for the future RPG visual layer.                                |

The model uses `extra="forbid"`. A typo'd key fails fast at load time.

## Applying a template

```bash
kompany template list
kompany template show saas-startup
kompany template apply saas-startup
kompany template apply saas-startup --budget 2500 --directive "..."
kompany template apply indie-tool --force      # overwrite an existing apply
```

Applying a template writes:

1. **`company_config`** — `template_id`, `mission`, `mission_title`,
   `initial_budget`, `enabled_agents` (JSON-encoded list),
   `agent_config_overrides` (JSON object), `rpg_theme`.
2. **`ledger`** — one `INCOME` row equal to the initial budget (or the
   `--budget` override).
3. **`projects`** — one `status='draft'` row per suggested directive
   (or one row carrying the `--directive` override). The `plan` JSON
   contains the template id, mission body, and the directive text.
4. **`audit_log`** — one `company.template_applied` event with the
   full payload.

Re-applying requires `--force`. Without it, `apply` raises
`TemplateAlreadyApplied` so a second click of an onboard button can't
silently double-fund the ledger.

## Authoring a community template

1. Create `kompany/src/kompany/templates/community/<your-id>/`.
2. Add `manifest.json` and `mission.md`.
3. Optionally add `suggested_directives.md`.
4. Make sure every `enabled_agents` entry is in the
   `KNOWN_AGENT_ROLES` set (`ceo, cfo, cto, cpo, cmo, cro, coo, csa,
   ciso, cos, cv`).
5. Run `pytest -q kompany/tests/test_templates.py` — the schema test
   will fail loudly on typos.
6. Open a PR.

Community templates are surfaced by `kompany template list` alongside
the built-ins. None ship by default — the `community/` directory is
just a placeholder.

## Four-surface API

| Surface | Operation                                                                                          |
|---------|----------------------------------------------------------------------------------------------------|
| CLI     | `kompany template list / show <id> / apply <id> [--force] [--budget=X] [--directive=...]`          |
| SDK     | `Kompany().templates.list() / show(id) / apply(id, force=..., override_budget=..., override_directive=...)` |
| REST    | `GET /templates`, `GET /templates/<id>`, `POST /templates/<id>/apply` (body: `{force, override_budget, override_directive}`) |
| MCP     | `kompany_template_list`, `kompany_template_show`, `kompany_template_apply`                         |

All four surfaces route to `KompanyEngine.apply_template`, which in turn
delegates to `Templates.apply`. There is no per-surface business logic.
