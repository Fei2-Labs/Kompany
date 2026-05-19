# Onboarding (`kompany onboard`)

The `kompany onboard` command is the one-line install path. It compresses
the legacy four-step setup — `pip install -e ".[api,mcp,dev]"`, drop a
`.env`, export an API key, run `kompany init` — into a single command:

```
uvx kompany onboard --yes
```

In under 90 seconds the wizard checks the environment, captures an LLM
provider + API key, applies a starter company template, and (optionally)
runs the founder's first directive. This is the path the demo video
exercises, and the path the integration test suite covers end-to-end.

## What it does, step by step

1. **Environment check** — verifies Python ≥ 3.11, creates the data dir
   (`~/.kompany` by default, or `--data-dir` override) with mode `0700`
   (defence-in-depth even though the database is already encrypted),
   and confirms the credential vault location is writable.
2. **LLM provider** — picks a provider (default `anthropic`; override
   with `--provider`), accepts an API key (via `--api-key`, the matching
   environment variable, or a masked interactive prompt), stores it in
   the encrypted vault when `KOMPANY_VAULT_KEY` is configured, and pings
   the provider once. The ping is skipped entirely under
   `KOMPANY_TEST_MODE=1`.
3. **Starter template** — lists `kompany.state.templates.Templates` and
   applies the chosen one (default `blank`; override with `--template`).
   Applying a template writes mission text + `enabled_agents` to
   `company_config`, ledgers the template's `initial_budget`, and stages
   suggested directives as draft projects.
4. **First directive (optional)** — when `--directive "..."` is passed,
   the wizard calls `KompanyEngine.process_directive(...)` and prints
   the first five lines of the CEO's response. Without the flag, the
   step is skipped under `--yes` and is opt-in under interactive mode.

After all four steps the wizard prints the next-step panel:

```
kompany inbox             — view pending approvals & decisions
kompany directive "..."   — send the team another instruction
kompany episodes list     — review completed project episodes
kompany template list     — browse other starter companies
kompany health list       — watchdog status & recovery events
```

## Headless vs interactive

| Flag                  | Effect when `--yes` is set                               |
| --------------------- | -------------------------------------------------------- |
| (none)                | provider=`anthropic`, template=`blank`, no directive     |
| `--provider=...`      | uses that provider; must be in `SUPPORTED_PROVIDERS`     |
| `--api-key=...`       | overrides env var; required if no env var is set         |
| `--template=...`      | applies that template id; unknown id → exit code 2       |
| `--directive="..."`   | runs one directive after setup                           |
| `--data-dir=/path`    | writes everything under `/path` instead of `~/.kompany`  |

When `--yes` is set:

- **Missing API key** → exit code 2 with a message naming the matching
  env var (e.g. `ANTHROPIC_API_KEY`).
- **Ping fails** → exit code 2; rerun interactively to retry, or fix
  the network/key and try again.
- **Existing install present** → "reuse" (no destructive action).

Without `--yes`, the wizard prompts for each missing field. The API-key
prompt uses `rich.prompt.Prompt.ask(..., password=True)` so the key is
never echoed.

## Idempotent re-run

If the data dir already contains a `kompany.db`, the wizard reads the
applied `template_id` and asks **Reuse / Overwrite / Cancel**. Under
`--yes` the default is **reuse**, so re-running never destroys state.

A *partial* install (data dir has artefacts but no usable `kompany.db`)
is treated as an overwrite candidate: under `--yes` we wipe and start
fresh; interactively we ask **Overwrite / Cancel**.

## Test-mode escape hatch

Set `KOMPANY_TEST_MODE=1` to skip the LLM ping. This is what the unit
and integration suites use so they don't need a real API key. Production
runs always issue a real ping.

## ASCII demo

```
$ uvx kompany onboard --yes

🎬 Welcome to Kompany. Let's set up your first company in 90 seconds.

[1/4] Checking environment...        ✓ Python 3.11+
                                     ✓ vault directory ~/.kompany
[2/4] LLM provider
      Anthropic API key: sk-ant-...
      Test call... ✓ claude-opus-4-7 reachable
[3/4] Choose a starter company       (uses 05-19-company-templates)
      › 1. SaaS startup
        2. Indie tool
        3. Consulting firm
        4. Content creator
        5. Ecommerce
        6. Blank
[4/4] First directive
      "Launch a paid Discord community for AI side-projects"

🚀 Your CEO is on it.
    See live progress: kompany inbox
    Stop anytime:      Ctrl+C
```

## Implementation pointers

- Wizard: `kompany/src/kompany/installer/onboard.py` (`run_onboard()`
  + four `_step_*` helpers).
- CLI entry: `kompany/src/kompany/interfaces/cli.py` →
  `@app.command() def onboard(...)`.
- Unit tests: `kompany/tests/test_onboard.py`.
- Integration tests: `kompany/tests/integration/test_onboard_flow.py`.
- Spec: `.trellis/tasks/05-19-one-line-install/prd.md`.
