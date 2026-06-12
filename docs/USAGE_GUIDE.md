# Kompany — Usage Guide

This guide covers everything you need to operate Kompany, from initializing your company to talking to the team through the CEO channel, running debates, and executing revenue projects.

## Table of Contents

1. [Installation](#installation)
2. [Configuration](#configuration)
3. [Initializing Your Company](#initializing-your-company)
4. [The CEO Channel](#the-ceo-channel)
5. [Understanding Directive Types](#understanding-directive-types)
6. [Checking Status](#checking-status)
7. [Working with Projects](#working-with-projects)
8. [Running Strategic Debates](#running-strategic-debates)
9. [Viewing the Ledger](#viewing-the-ledger)
10. [Executing Projects](#executing-projects)
11. [Execution: Model Source & Harness Sessions](#execution-model-source--harness-sessions)
12. [Running 24/7: The Kompany Daemon](#running-247-the-kompany-daemon)
13. [Self-Update: Governed Code Changes](#self-update-governed-code-changes)
14. [Channels: Talk to Your Company Anywhere](#channels-talk-to-your-company-anywhere)
13. [Anima: The Company's Persona](#anima-the-companys-persona)
13. [Using the REST API](#using-the-rest-api)
14. [Using the MCP Server](#using-the-mcp-server)
15. [Using the Python SDK](#using-the-python-sdk)
16. [Using as a Claude Code Skill](#using-as-a-claude-code-skill)
17. [Cost Management](#cost-management)
18. [Agent Memory](#agent-memory)
19. [Best Practices](#best-practices)
20. [Troubleshooting](#troubleshooting)

---

## Installation

### Prerequisites

- Python 3.11 or higher
- An API key for at least one [supported LLM provider](../README.md#multi-provider-llm-support)

### Setup

```bash
git clone https://github.com/Fei2-Labs/Kompany.git
cd Kompany/kompany

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

# Install the package
pip install -e .

# For REST API support
pip install -e ".[api]"

# For MCP server support
pip install -e ".[mcp]"

# For development (tests)
pip install -e ".[dev]"
```

### Set Your API Key

```bash
export ANTHROPIC_API_KEY=your-key-here
```

Or create a `.env` file in the `kompany/` directory:

```
ANTHROPIC_API_KEY=your-key-here
```

To use other providers, set their API keys as well:

```bash
export OPENAI_API_KEY=sk-...       # For GPT-4o, o3, etc.
export GEMINI_API_KEY=...          # For Gemini models
export GLM_API_KEY=...             # For GLM (Zhipu AI)
export KIMI_API_KEY=...            # For Moonshot/Kimi
```

### Verify Installation

```bash
kompany --help
```

You should see the available commands, including the core set: `init`, `directive`, `channel`, `status`, `projects`, `project`, `debate`, `ledger`, `execute`.

---

## Configuration

### Environment Variables

| Variable | Description | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key | Yes (if using Claude models) |
| `OPENAI_API_KEY` | OpenAI API key | No |
| `GEMINI_API_KEY` | Google Gemini API key | No |
| `GLM_API_KEY` | Zhipu AI (GLM) API key | No |
| `KIMI_API_KEY` | Moonshot (Kimi) API key | No |
| `CUSTOM_LLM_API_KEY` | Custom endpoint API key | No |
| `CUSTOM_LLM_BASE_URL` | Custom OpenAI-compatible base URL | No |
| `KOMPANY_DB_PATH` | SQLite database path | No (defaults to `./kompany.db`) |
| `KOMPANY_CONFIG_PATH` | YAML config file path | No |

### Company Config (YAML)

You can optionally provide a YAML config file:

```yaml
company:
  name: "Your Company"
  capital: 50
  goal: "One-line company goal"
  time_horizon: "6 months"
  exclusions: "gambling, weapons"

# Override model tiers (optional)
models:
  apex: "claude-opus-4-20250514"
  primary: "claude-sonnet-4-20250514"
  economy: "claude-haiku-4-20250414"

# Custom OpenAI-compatible endpoint (optional)
custom_llm:
  base_url: "https://my-endpoint.example.com/v1"
  api_key: "my-key"
```

If no config file exists, use `kompany init` to set up your company interactively.

---

## Initializing Your Company

Before sending any directives, initialize your company:

```bash
kompany init --name "Acme SaaS" --capital 50 --goal "AI invoice reconciliation" --time-horizon "6 months" --exclusions "gambling, weapons"
```

| Option | Description | Default |
|---|---|---|
| `--name` | Company name | *(prompted)* |
| `--capital` | Starting capital in euros | *(prompted)* |
| `--goal` | One-line company goal | *(prompted)* |
| `--time-horizon` | Planning time horizon | *(prompted)* |
| `--exclusions` | Sectors or activities to exclude | *(prompted)* |

This creates the SQLite database, sets your starting capital, and records your company profile. If you omit any option, you'll be prompted interactively.

**What happens under the hood:**
- SQLite database is created with schema (ledger, projects, tasks, decisions, agent memory)
- Starting capital is recorded as an `income` entry in the ledger
- Company config is stored in the `company_config` table

---

## The CEO Channel

Every founder message goes into the **CEO channel** — the founder's conversation surface with the team, conducted by the CEO. The `directive` command opens or continues a channel session; in the web UI it is the `CHANNEL // CEO` thread above the directive bar. Send any natural-language instruction:

```bash
# Acquisition — triggers budget check + revenue project if needed
kompany directive "Buy a Mac Studio M4 128GB, budget €50"

# Strategic — CEO analyzes or triggers full debate
kompany directive "Should we pivot from B2C to B2B?"

# Operational — CEO delegates to the appropriate agent via KompanyEngine
kompany directive "Set up a CI/CD pipeline for the main repo"

# Informational — no AI cost, mechanical response
kompany directive "What's our current balance?"
```

### How the CEO routes each message

The CEO **auto-routes** every message into one of three modes:

- **execute** — clear intent → the directive is dispatched down the pipeline.
- **clarify** — ambiguous → the CEO asks you a follow-up question instead of guessing. Your reply continues the **same session** (grill-style requirements discovery), capped at 5 clarify turns so it can never loop forever. The conversation converges to a dispatch (or an answer).
- **answer** — a pure question (e.g. "我们现在余额多少？" / "what's our balance?") → the CEO replies only; no project is created and nothing is dispatched.

The exchange renders as a persistent conversation thread with full cost visibility (estimated cost up front, live cost while executing, final cost on the reply).

### The spend gate (gated GO)

Before an expensive or irreversible action runs, the channel pauses on a **spend gate**:

- `auto` / `ceo` approval tier **and** estimated cost at or under the founder threshold (default **$1**) → runs immediately, cost streams live.
- `master` tier **or** estimated cost **over** the threshold → the CEO posts a **preview turn** (plan + estimated cost) and pauses. **Nothing executes until you reply GO.** You can also abandon the gated session.

```bash
# Interactive mode answers clarify questions and GO/abandon prompts inline:
kompany directive "launch the paid campaign, budget €40" -i

# One-shot mode prints the clarify question or preview + a session id you can
# continue with --session:
kompany directive "set up the thing"          # → prints clarify + session id
kompany directive "the email one" --session <session-id>   # continues it
```

Gated sessions are parked decisions: they persist server-side and survive an engine restart, so a GO still works after the desktop app relaunches.

### Inspecting channel history

```bash
kompany channel sessions            # list sessions, newest first
kompany channel sessions --state clarifying
kompany channel show <session-id>   # full thread (founder + CEO turns)
```

**What happens under the hood:**

1. The message opens or continues a CEO-channel session (a closed session rejects further sends — start a new message).
2. The CEO agent classifies the message (type, route, estimated cost, agents needed, approval tier).
3. Route detection picks execute / clarify / answer; the autonomy gate + spend gate check the approval tier and cost.
4. For ACQUISITION: CFO checks budget → shortfall triggers revenue project creation
5. For STRATEGIC: CEO analysis or full multi-agent debate
6. For OPERATIONAL: CEO breaks into steps and delegates via KompanyEngine. CoS coordinates cross-functional issues.
7. For INFORMATIONAL: CFO responds mechanically (no LLM cost)
8. All AI costs are recorded in the ledger; cross-links (`approval_id` → INBOX, `project_id` → EPISODES) appear in the channel thread.
9. The CEO's reply is recorded as a turn with its cost.

---

## Understanding Directive Types

The CEO classifies every directive into one of four types:

### ACQUISITION

Triggered by: "Buy X", "Get X", "Hire X", "Purchase X"

The system **must deliver** what was requested. If the budget is insufficient, the CEO creates a revenue project with concrete revenue paths instead of downgrading the mission.

```bash
kompany directive "Hire a freelance designer for the landing page, budget €500"
```

### STRATEGIC

Triggered by: "Should we X?", "What's our approach to Y?", questions requiring analysis

For simple questions, the CEO provides analysis directly. For complex strategic questions, use the `debate` command for a full multi-agent debate.

```bash
kompany directive "Should we raise a Series A or extend runway through revenue?"
```

### OPERATIONAL

Triggered by: "Set up X", "Configure Y", "Deploy Z", "Create X"

The CEO breaks the directive into action steps and delegates to the appropriate agent via KompanyEngine. CoS coordinates cross-functional issues.

```bash
kompany directive "Set up monitoring and alerting for our production API"
```

### INFORMATIONAL

Triggered by: "What's our balance?", "Status", "How many projects?"

Queries the state directly. The CFO responds mechanically — **no AI cost**.

```bash
kompany directive "What's our total AI spending so far?"
```

---

## Checking Status

View the current state of your company:

```bash
kompany status
```

Output includes:
- Company name and goal
- Current balance
- Total income, expenses, and AI costs
- Number of active projects

---

## Working with Projects

### List All Projects

```bash
kompany projects
```

Shows project ID, name, type (revenue/operational/strategic), status, target amount, and funded amount.

### View Project Details

```bash
kompany project abc12345
```

Shows full project details including:
- Revenue paths and plan
- Assigned agents
- Task breakdown and status
- Target vs. funded amounts

---

## Running Strategic Debates

For complex strategic questions, run a full multi-agent debate:

```bash
kompany debate "Should we build SSO or focus on self-serve onboarding?"
```

### Debate Protocol

The debate follows a structured multi-round protocol:

**Round 1 — Independent Positions**
Each agent argues from their domain without seeing other positions. Output includes domain-specific analysis, a recommendation, and confidence level.

**Round 2 — Rebuttal & Challenge**
Each agent sees all Round 1 positions. They must acknowledge valid points, challenge disagreements, and update their position if warranted.

**Round 3 — Convergence** *(3-round mode only)*
Agents move toward consensus. They state concessions and any non-negotiable hard lines. Skipped in 2-round mode (solo/pre-seed stages).

**CoS Synthesis**
The Chief of Staff produces a structured CEO brief: consensus position, key tensions, recommended option, risk flags, and the decision required.

**CEO Decision**
The CEO reviews everything and makes the final call: decision, rationale, tradeoffs, overrides, next steps, confidence score, and reversibility assessment.

### Stage-Based Configuration

| Stage | Agents | Rounds | Max Cost |
|---|---|---|---|
| Solo | CEO, CTO, CPO, CFO, CoS | 2 | ~$0.50 |
| Pre-seed | CEO, CTO, CPO, CoS, CV (Brand visuals & visual direction) | 2 | ~$0.75 |
| Seed | CEO, CTO, CPO, CMO, CRO, CoS, CV (Brand visuals & visual direction) | 3 | ~$1.50 |
| Series A | All 11 agents | 3 | ~$2.00 |

---

## Viewing the Ledger

See all financial transactions including AI costs:

```bash
kompany ledger
kompany ledger --limit 25
```

Each entry shows: timestamp, amount, balance after, description, and category.

Categories:
- `income` — Funds added to the company
- `expense` — Purchases, payments
- `ai_cost` — LLM API calls (automatically tracked)
- `allocation` — Funds reserved for projects
- `refund` — Returned funds

---

## Executing Projects

Once a revenue project is created, execute its tasks autonomously:

```bash
kompany execute abc12345
```

The `ProjectRunner` handles execution:

1. Decomposes the project into tasks based on the plan
2. Assigns each task to the appropriate subagent:
   - **Analyst** — Financial modeling, ROI analysis
   - **Builder** — Code generation, prototyping
   - **Procurement** — Vendor research, price comparison
   - **Researcher** — Market research, data gathering
   - **Writer** — Content creation, copywriting
3. Runs tasks and tracks AI cost per task
4. Updates project status and reports results

---

## Execution: Model Source & Harness Sessions

Tasks don't have to be single LLM calls. When you configure a **model source**, every project task runs as a real multi-turn agentic session — tools, file edits, a persistent per-project workspace — executed via the Claude Code CLI, the Codex CLI, or opencode, depending on the source you pick. You choose where the work runs and how it's billed; the engine derives the rest.

### Choosing a Model Source

| Source kind | How tasks execute | Billing |
|---|---|---|
| `custom_api` | via the opencode CLI with your API key | `api` — real per-token expense |
| `claude_subscription` | via the Claude Code CLI on your Claude subscription | `subscription` — monthly fee |
| `openai_subscription` | via the Codex CLI on your OpenAI subscription | `subscription` — monthly fee |

Configure it on any surface:

- **Settings page** (web UI / desktop app) — the `MODEL SOURCE` section probes your installed CLIs, lets you pick a source (and a monthly fee for subscriptions), and shows a plain-language summary of how work will run.
- **Onboarding** — interactive onboarding auto-detects installed agent CLIs (`claude`, `codex`, `opencode`) and offers detected subscriptions as options. Picking the Claude subscription is fully zero-key (single-shot calls route through the `claude` CLI too); picking the OpenAI subscription still asks for an API key for the C-suite's single-shot calls — only task execution rides the Codex CLI. The default stays the API-key path; nothing changes unless you opt in.
- **CLI:**

  ```bash
  kompany model-source show                                       # active source (or "not configured")
  kompany model-source set --kind claude_subscription --monthly-fee 20
  kompany model-source set --kind custom_api
  kompany model-source set --clear                                # back to legacy per-token billing
  kompany model-source detect                                     # probe PATH for claude / codex / opencode
  ```

- **REST:** `GET /settings/model-source`, `PUT /settings/model-source` (body: `kind`, optional `billing_mode` / `monthly_fee_usd` / `price_overrides`; `kind: null` clears), `GET /settings/detect-clis`.
- **MCP:** `kompany_model_source_show`, `kompany_model_source_set` (pass `clear: true` to remove), `kompany_detect_clis`.
- **SDK:** `k.model_source()`, `k.set_model_source({...})` (`None` clears), `k.detect_clis()`.

The serialized source always includes a read-only `execution_summary` describing how work runs. There is deliberately no input for picking the execution loop — it follows from the source kind.

### Billing Modes

- **`api`** (default for `custom_api`) — every call books real per-token cost to the ledger, exactly like single-shot calls. Use `price_overrides` (per-model `[input_usd, output_usd]` per million tokens) to fill or override pricing for private deployments.
- **`subscription`** (default for the subscription kinds; `monthly_fee_usd` required) — the monthly fee is your real expense, booked to the ledger once per calendar month (idempotent — restarts and repeated heartbeats never double-book it). Per-call real cost is 0: individual sessions never touch the balance. Each session still records its tokens plus an API-equivalent **shadow value** (surfaced in `llm.spend` events with `shadow: true` and kept in the shadow-cost record) so you and the team keep quota awareness and can judge whether the subscription pays for itself.

You can override the default pairing — e.g. run a `claude_subscription` source with `billing_mode=api` during a pay-as-you-go trial month.

### Per-Task Budget Caps

At project decomposition the CEO assigns each task a budget cap and a turn cap:

- Default: **$0.50** per task. The CEO may assign up to the **$5.00** ceiling for genuinely complex work — never more (clamped when the task is written). Default turn cap: 30.
- A founder-approved budget increase (see below) can raise a task's cap **past the CEO ceiling** — your approval is authoritative and is never re-clamped at execution time.
- The project's budget envelope is the hard outer cap: a task never spends more than the envelope's remaining balance, and an exhausted envelope parks tasks instead of running them.

### The Approval Inbox During Sessions

Three kinds of cards land in the INBOX from harness execution:

- **`harness_permission` — a session asks to use a side-effecting tool.** The session pauses on the tool call and waits about 120 seconds for your decision. Approve within the window → the tool call proceeds mid-run. Reject → the tool is denied (with your reason) and the task classifies as blocked, listing the denied tools. No decision in time → the tool is denied for now, but **the request stays in your inbox**: approve it later and the task's next run redeems the approval instantly. Approvals are one-shot — each covers exactly one ask; a later identical ask files a fresh request.
- **`project_envelope_topup` — the project's budget envelope is exhausted.** Tasks are parked (never refused) and one top-up card per project shows the suggested amount. Approving actually moves money: the envelope is funded from unallocated treasury and parked tasks run on the project's next execution. If the treasury can't cover it yet, the approval stands — it retries once funds free up. Rejecting parks the project with explicit next steps.
- **`harness_budget_increase` — a session hit its per-task cap.** The run pauses (the session is saved) and the card shows the cap, the amount spent, and the proposed increase. Approving raises the task's cap for real — re-run the project and the session continues from where it stopped. The payload slots `approved_top_up_usd` / `approved_increase_usd` are honored ahead of the suggested amounts — the hook for founder-edited amounts in a future inbox UI.

### If the CLI Is Missing

If the CLI for your chosen source isn't on PATH (say you picked the Claude subscription but `claude` isn't installed), nothing crashes: the engine records a `harness_vehicle_missing` health event with an install hint, and tasks fall back to the legacy single-shot, text-only mode until you install the CLI or switch the source in Settings.

---

## Running 24/7: The Kompany Daemon

The CLI and desktop app only act while you have them open. The **daemon** keeps the same engine running around the clock, so the company advances work, books fees, and queues notifications with nobody watching.

```bash
kompany daemon run         # run the server in the foreground (Ctrl-C to stop)
kompany daemon install     # install as a launchd LaunchAgent (macOS) — survives reboots
kompany daemon status      # live server + launchd + ticker report
kompany daemon uninstall   # unload and remove the LaunchAgent
```

These four commands are deliberately CLI-only — they manage a process on *this* machine. Tick visibility lives on the existing surfaces instead: `kompany status` (and `GET /status`, `kompany_status`, `k.status()`) carries a `ticker` block (`running`, `last_tick_at`, `tick_count`, `interval_seconds`), and `kompany observability` shows the most recent ticks.

### One engine, shared by app and daemon

Exactly one Kompany server runs per data directory — the discovery file `<data_dir>/server.json` is the lock:

- `kompany daemon run` refuses to start when a healthy server already owns it (and tells you its pid and port).
- The desktop app **attaches**: if it finds a healthy running server (typically the daemon), it points its window at that server instead of launching a second one — and leaves it running when you close the app. With no server running, the app spawns its own exactly as before.

So the app's live activity panel always shows daemon-driven work, and the daemon keeps working after the app quits.

### What a tick does

Every `tick_interval_seconds` (default 300 — every 5 minutes) the engine wakes for one **tick**, its autonomous heartbeat:

1. **Heartbeat** — checks pending approvals and active projects, books the monthly subscription fee (at most once per calendar month), and prepares notifications.
2. **Advance work** — runs at most **one** pending task of one active project, under every existing safety rail: per-task budget caps, the project's budget envelope, and the approval inbox. A project waiting on a pending budget top-up or budget-increase approval is skipped (the team never grinds against a closed gate), and failed tasks are never auto-retried — re-running those stays your decision. Set `daemon_auto_execute: false` (top-level YAML key) to keep ticking without autonomous task execution.
3. **Housekeeping** — prunes tick history (the last 500 ticks are kept) and trims old episodes.

Spend stays bounded with nobody watching: one task slice per tick × per-task caps × envelope hard caps, and anything gated waits in your INBOX.

### The brake: suspend

`kompany suspend` is the daemon brake. While suspended, every tick records itself as idle and does nothing else — no heartbeat, no task execution. `kompany resume` wakes everything up.

One billing consequence to know: because the heartbeat doesn't run while suspended, **the monthly subscription fee is not booked during suspension** — it books (idempotently, at most once per calendar month) on the first tick after you resume.

### Install details (macOS)

`kompany daemon install` writes `~/Library/LaunchAgents/com.kompany.daemon.plist` with `KeepAlive` and `RunAtLoad`, pins `KOMPANY_DATA_DIR` to the data directory chosen at install time, and logs to `<data_dir>/logs/daemon.out.log` / `daemon.err.log`. The launch command prefers the server binary bundled inside `/Applications/Kompany.app` (no Python install needed); without the desktop app it falls back to your current Python interpreter. On non-macOS platforms `install` exits with a clear macOS-only message — run `kompany daemon run` under your own process supervisor instead.

---

## Using the REST API

Start the API server (requires `pip install -e ".[api]"`):

```bash
uvicorn kompany.interfaces.api:app --reload
```

The API runs at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

### Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/init` | Initialize a new company |
| `POST` | `/channel/send` | Send a message into the CEO channel (`text`, optional `session_id`) — canonical |
| `POST` | `/directive` | Backward-compat alias for `/channel/send` (same handler, same `session_id` passthrough) |
| `GET` | `/channel/sessions` | List channel sessions, newest first (optional `state`, `limit`) |
| `GET` | `/channel/sessions/{session_id}` | One session + its ordered turns (the full thread) |
| `POST` | `/channel/sessions/{session_id}/go` | Founder GO on a spend-gated session |
| `POST` | `/channel/sessions/{session_id}/abandon` | Abandon a session without executing |
| `GET` | `/channel/runs/{run_id}/cost` | Authoritative per-run AI cost (reload-restore reconcile) |
| `GET` | `/status` | Get company status |
| `GET` | `/projects` | List active projects |
| `GET` | `/projects/{project_id}` | Get a specific project |
| `GET` | `/ledger?limit=10` | Get recent ledger entries |
| `POST` | `/projects/{project_id}/execute` | Execute a project's tasks |

A `/channel/send` (or `/directive`) response carries a `status` that may be `completed`, `clarify` (the CEO asks back — re-POST with the returned `session_id`), `gated` (spend gate — call `/go` or `/abandon`), `answered`, `abandoned`, `suspended`, or `failed`, plus `session_id` and `run_id`.

### Examples

```bash
# Initialize
curl -X POST http://localhost:8000/init \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme", "capital": 50, "goal": "AI tools", "time_horizon": "6 months", "exclusions": "gambling"}'

# Send a message into the CEO channel
curl -X POST http://localhost:8000/channel/send \
  -H "Content-Type: application/json" \
  -d '{"text": "Buy a Mac Studio M4 128GB, budget €50"}'

# Continue a clarify session (re-use the returned session_id)
curl -X POST http://localhost:8000/channel/send \
  -H "Content-Type: application/json" \
  -d '{"text": "the M4 Max, 128GB", "session_id": "abc12345"}'

# GO on a spend-gated session
curl -X POST http://localhost:8000/channel/sessions/abc12345/go

# Check status
curl http://localhost:8000/status

# View ledger
curl "http://localhost:8000/ledger?limit=20"
```

---

## Using the MCP Server

Run as an MCP server for Claude Code, Cursor, or any MCP-compatible client (requires `pip install -e ".[mcp]"`):

```bash
kompany-mcp
# or
python -m kompany.interfaces.mcp_server
```

### Available Tools

| Tool | Parameters | Description |
|---|---|---|
| `kompany_init` | `name`\*, `capital`, `goal`\*, `time_horizon`, `exclusions` | Initialize a new company |
| `kompany_directive` | `text`\*, `session_id` | Send a message into the CEO channel (pass `session_id` to continue a clarify session) |
| `kompany_channel_sessions` | `state`, `limit` | List channel sessions, newest first |
| `kompany_channel_session` | `session_id`\* | One session + its ordered turns (full thread) |
| `kompany_channel_go` | `session_id`\* | Founder GO on a spend-gated session |
| `kompany_channel_abandon` | `session_id`\* | Abandon a session without executing |
| `kompany_status` | *(none)* | Get company status |
| `kompany_projects` | *(none)* | List active projects |
| `kompany_project` | `project_id`\* | Get project details |
| `kompany_ledger` | `limit` | Get recent ledger entries |
| `kompany_debate` | `question`\* | Run a multi-agent debate |
| `kompany_execute` | `project_id`\* | Execute a project's tasks |

### Claude Code Configuration

Add to your Claude Code MCP settings:

```json
{
  "mcpServers": {
    "kompany": {
      "command": "kompany-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "your-key-here"
      }
    }
  }
}
```

---

## Using the Python SDK

```python
from kompany import Kompany

# Initialize
k = Kompany()
k.init(name="Acme", capital=50, goal="AI tools", time_horizon="6 months", exclusions="gambling")

# Send a message into the CEO channel
result = k.directive("Buy a Mac Studio M4 128GB, budget €50")
print(result["message"])
print(f"AI cost: ${result['total_ai_cost']:.2f}")

# The channel namespace mirrors the REST /channel/* surface:
result = k.channel.send("set up the thing")
if result["status"] == "clarify":
    # The CEO asked a question — continue the same session.
    result = k.channel.send("the email one", session_id=result["session_id"])
if result["status"] == "gated":
    # Spend gate — review the plan + estimate, then GO (or abandon).
    result = k.channel.go(result["session_id"])
for s in k.channel.sessions():          # list sessions, newest first
    print(s["session_id"], s["state"])
thread = k.channel.session(result["session_id"])   # full thread

# Check balance
print(f"Balance: €{k.balance():.2f}")

# View status
status = k.status()
print(f"Active projects: {status['active_projects']}")

# List projects
for project in k.projects():
    print(f"  {project['id']}: {project['name']} ({project['status']})")

# Get project details
project = k.project("abc12345")

# View ledger
for entry in k.ledger(limit=5):
    print(f"  {entry['description']}: {entry['amount']}")

# Execute a revenue project
result = k.execute_project("abc12345")
```

### SDK Methods

| Method | Returns | Description |
|---|---|---|
| `Kompany(config_path=None)` | — | Constructor |
| `init(name, capital=0.0, goal="", time_horizon="", exclusions="")` | `None` | Initialize a new company |
| `directive(text, session_id=None)` | `dict` | Send a message into the CEO channel, get the result |
| `channel.send(text, session_id=None)` | `dict` | Send a message (opens/continues a session) |
| `channel.go(session_id)` | `dict` | Founder GO on a spend-gated session |
| `channel.abandon(session_id)` | `dict` | Abandon a session without executing |
| `channel.sessions(state=None, limit=50)` | `list[dict]` | List channel sessions, newest first |
| `channel.session(session_id)` | `dict \| None` | One session + its turns (full thread) |
| `status()` | `dict` | Company status |
| `projects()` | `list[dict]` | All active projects |
| `project(project_id)` | `dict \| None` | A specific project |
| `balance()` | `float` | Current balance |
| `ledger(limit=10)` | `list[dict]` | Recent ledger entries |
| `execute_project(project_id)` | `dict` | Execute a project autonomously |

---

## Using as a Claude Code Skill

Kompany ships as a Claude Code skill. Invoke it directly in any Claude Code session:

```
/kompany "Buy a Mac Studio M4 128GB, budget €50"
/kompany "What's our balance?"
/kompany "Should we build SSO or focus on self-serve onboarding?"
```

The skill file is at `.claude/skills/kompany/SKILL.md`. It activates the venv, ensures the engine is installed, and routes your directive through `kompany directive`.

---

## Self-Update: Governed Code Changes

Kompany can change its own code — but never the copy that is running, and never without you. The flow implements the constitution's "Source code self-modification" clause:

```bash
kompany self-update propose "Fix the dashboard date format and add a regression test"
kompany self-update list
kompany self-update show <id>
```

What happens on `propose`:

1. A dedicated **clone** of the Kompany repo is created under `<data_dir>/self_update/repo` (the running checkout is constitutionally off-limits to sessions).
2. A harness session implements the change on a fresh `self-update/<id>` branch, under its own budget cap (`self_update_budget_cap_usd`, default $2) and a mandatory-regression-test contract.
3. The REAL diff is tier-checked. Protected paths (the constitution, the ledger and cost-tracking code, the approval/autonomy code, the self-update pipeline itself, CI workflows) abort the proposal outright — the branch is discarded and a health event is recorded. The brakes can't modify the brakes.
4. The test suite runs inside the clone. Red tests don't hide the proposal — the card says `tests: FAILED` and you decide.
5. A `self_update_proposal` card lands in your inbox with the diff stat, files, test summary, and session cost.

What happens on **approve**: the branch is pushed to origin and a GitHub PR is opened when `gh` is available (otherwise you open it manually — the push result is on the card). **Merging stays on GitHub, in your hands.** After you merge, rebuild and reinstall with the existing scripts. Reject keeps the branch local for autopsy.

The same operation is available everywhere: REST `POST /self-update/propose`, MCP `kompany_self_update_propose`, SDK `k.self_update_propose(...)`.

---

## Anima: The Company's Persona

Anima (provisional name, tracked in the glossary) is the persona layer above the C-suite: an explicit emotional state plus a private first-person diary, driven by the daemon tick loop.

- **Emotion is voice-only.** Income, completed/failed tasks, and alarm health events nudge a 2-axis state (valence, energy) in pure code — no LLM. Per the constitution's honest-assessment clause, this state shapes Anima's *voice* (diary tone) only; it is never injected into C-suite, classification, or debate prompts.
- **Diary: once per day.** After the day's first tick, one economy-tier LLM call distills the last 24h (tasks, ledger, health events, decided approvals) into a <=200-word entry whose tone reflects the current emotion. Cost books through the normal path (`action_type="anima_diary"`). A suspended company writes no diary. Publishing the diary externally (X/Weibo/Telegram) comes in a later release behind its own approval gate.

```bash
kompany anima state    # valence, energy, derived tone
kompany anima diary    # recent entries, newest first
```

The same operations exist on every interface: REST `GET /anima/state` + `GET /anima/diary`, MCP `kompany_anima_state` / `kompany_anima_diary`, SDK `k.anima_state()` / `k.anima_diary()`. Config flags: `anima_enabled` (whole layer) and `anima_diary_enabled` (just the daily LLM call), in YAML or `KOMPANY_ANIMA_ENABLED` / `KOMPANY_ANIMA_DIARY_ENABLED`.

---

## Channels: Talk to Your Company Anywhere

Channels connect outside transports to the same CEO conversation engine the app and CLI use — adapters translate; the engine reasons.

**Telegram (chat with the CEO).** Set `TELEGRAM_BOT_TOKEN` (BotFather) and `TELEGRAM_ALLOWED_CHAT_IDS` (comma-separated; anything else is ignored). Any server boot (app, daemon) starts the long-poll worker automatically. Each chat keeps its own conversation session: clarify questions come back as replies, spend gates ask for `GO`, duplicate updates are replay-protected.

**Outbox (drafts-only publishing).** When `anima_outbox_enabled` is on, the daily diary also files an outbox draft. Every draft becomes a `channel_post` approval card — approving marks it ready and you copy/post manually. Nothing auto-posts in this release; auto-posting to X/Weibo lands later behind its own integration and the same approval gate.

**Email triage (inbound).** Set `email_imap_host` / `email_imap_user` (password in the credential vault as `email_imap_password`). The daemon polls every N ticks (`email_poll_every_ticks`, default 12) and files one read-only triage card per new mail — you decide what becomes a directive.

Status anywhere: `kompany channels status`, `kompany channels outbox`, REST `/channels/*`, MCP `kompany_channels_*`, SDK.

---

## Cost Management

### How Costs Work

Every LLM API call is tracked as a real business expense. The `CostTracker` records each call to the ledger with:

- Model used (Opus, Sonnet, Haiku)
- Input and output token counts
- Calculated cost based on model pricing
- Description of what the call was for

If you configured a subscription model source, per-call spend is recorded as shadow value instead and the monthly fee is the real expense — see [Execution: Model Source & Harness Sessions](#execution-model-source--harness-sessions).

### Model Pricing

| Model | Input (per 1M tokens) | Output (per 1M tokens) | Used By |
|---|---|---|---|
| claude-opus-4 | $15.00 | $75.00 | CEO (apex tier) |
| claude-sonnet-4 | $3.00 | $15.00 | All other agents (primary tier) |
| claude-haiku-4 | $1.00 | $5.00 | Fallback (economy tier) |

### Monitoring Costs

After each directive, the AI cost is displayed:

```
AI cost for this directive: $0.18
  CEO classify:      $0.03
  CEO revenue plan:  $0.15
Balance before: €50.00 → Balance after: €49.82
```

Use `kompany ledger` to see all AI cost entries in the transaction history.

### Cost-Saving Tips

- Use INFORMATIONAL directives for status checks — they cost nothing (mechanical CFO)
- The `solo` stage uses fewer agents in debates (~$0.50 vs. ~$2.00 for Series A)
- Simple directives cost ~$0.03–$0.20; full debates cost ~$0.50–$2.00

---

## Agent Memory

Each agent has persistent memory stored in SQLite. After processing directives, agents can store learnings:

- Key insights from research or analysis
- Positions taken and outcomes observed
- Company-specific knowledge accumulated over time

Memory is scoped per-agent and persists across directives, allowing agents to build context over time.

---

## Best Practices

### Writing Good Directives

**Specific and actionable:**
- "Buy a Mac Studio M4 Ultra 128GB, budget €50"
- "Should we build SSO first or focus on self-serve onboarding for Q2?"
- "Hire a freelance React developer for 2 weeks, budget €3,000"

**Vague (less effective):**
- "Grow the company" — too broad
- "Make money" — no constraints
- "What should we do?" — no context

### Tips

1. **Include budget constraints** — Forces the CEO to make realistic plans
2. **Be specific about acquisitions** — "Buy X" with a price gives the CEO clear parameters
3. **Use debates for real tradeoffs** — "A vs B" questions produce the best debates
4. **Check the ledger regularly** — `kompany ledger` shows where AI costs are going
5. **Start with `solo` stage** — Minimizes debate costs while you're learning the system

---

## Troubleshooting

### "API key not found"

Set `ANTHROPIC_API_KEY` in your environment or in a `.env` file in the `kompany/` directory.

```bash
export ANTHROPIC_API_KEY=your-key-here
```

### "Company not initialized"

Run `kompany init` before sending directives:

```bash
kompany init --name "My Company" --capital 50 --goal "My product" --time-horizon "6 months" --exclusions "none"
```

### "Model not available"

Your API plan may not include Opus. The CEO agent will need Opus for optimal performance. Check your Anthropic dashboard for model access.

### Database issues

The SQLite database is created at the path specified by `KOMPANY_DB_PATH` (defaults to `./kompany.db` in the current directory). If you run `kompany` from different directories, it may create separate databases. Use an absolute path in `KOMPANY_DB_PATH` to avoid this.

### Tests failing

```bash
cd kompany
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

All tests should pass. If not, ensure you're running Python 3.11+ and have the latest dependencies installed.
