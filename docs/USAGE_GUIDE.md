# Kompany — Usage Guide

This guide covers everything you need to operate Kompany, from initializing your company to sending directives, running debates, and executing revenue projects.

## Table of Contents

1. [Installation](#installation)
2. [Configuration](#configuration)
3. [Initializing Your Company](#initializing-your-company)
4. [Sending Directives](#sending-directives)
5. [Understanding Directive Types](#understanding-directive-types)
6. [Checking Status](#checking-status)
7. [Working with Projects](#working-with-projects)
8. [Running Strategic Debates](#running-strategic-debates)
9. [Viewing the Ledger](#viewing-the-ledger)
10. [Executing Projects](#executing-projects)
11. [Using the REST API](#using-the-rest-api)
12. [Using the MCP Server](#using-the-mcp-server)
13. [Using the Python SDK](#using-the-python-sdk)
14. [Using as a Claude Code Skill](#using-as-a-claude-code-skill)
15. [Cost Management](#cost-management)
16. [Agent Memory](#agent-memory)
17. [Best Practices](#best-practices)
18. [Troubleshooting](#troubleshooting)

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

You should see all 8 commands listed: `init`, `directive`, `status`, `projects`, `project`, `debate`, `ledger`, `execute`.

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

## Sending Directives

The `directive` command is the primary way to interact with Kompany. Send any natural-language instruction:

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

**What happens under the hood:**

1. The CEO agent classifies the directive (type, urgency, estimated cost, agents needed, approval tier)
2. The autonomy gate checks the approval tier
3. The directive is routed to the appropriate handler
4. For ACQUISITION: CFO checks budget → shortfall triggers revenue project creation
5. For STRATEGIC: CEO analysis or full multi-agent debate
6. For OPERATIONAL: CEO breaks into steps and delegates via KompanyEngine. CoS coordinates cross-functional issues.
7. For INFORMATIONAL: CFO responds mechanically (no LLM cost)
8. All AI costs are recorded in the ledger
9. Result is displayed with cost transparency

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
| `POST` | `/directive` | Send a directive |
| `GET` | `/status` | Get company status |
| `GET` | `/projects` | List active projects |
| `GET` | `/projects/{project_id}` | Get a specific project |
| `GET` | `/ledger?limit=10` | Get recent ledger entries |
| `POST` | `/projects/{project_id}/execute` | Execute a project's tasks |

### Examples

```bash
# Initialize
curl -X POST http://localhost:8000/init \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme", "capital": 50, "goal": "AI tools", "time_horizon": "6 months", "exclusions": "gambling"}'

# Send a directive
curl -X POST http://localhost:8000/directive \
  -H "Content-Type: application/json" \
  -d '{"text": "Buy a Mac Studio M4 128GB, budget €50"}'

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
| `kompany_directive` | `text`\* | Send a natural language directive |
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

# Send a directive
result = k.directive("Buy a Mac Studio M4 128GB, budget €50")
print(result["message"])
print(f"AI cost: ${result['total_ai_cost']:.2f}")

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
| `directive(text)` | `dict` | Send a directive, get the result |
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

## Cost Management

### How Costs Work

Every LLM API call is tracked as a real business expense. The `CostTracker` records each call to the ledger with:

- Model used (Opus, Sonnet, Haiku)
- Input and output token counts
- Calculated cost based on model pricing
- Description of what the call was for

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
