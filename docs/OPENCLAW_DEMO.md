---
name: kompany-demo
description: >
  Step-by-step demo instruction for running Kompany with OpenClaw or any
  AI agent platform. Covers installation, multi-provider LLM setup,
  company initialization, directive execution, debate, and debugging.
tags: [demo, setup, multi-provider, openclaw, agent-compatible]
---

# Kompany — Demo Instruction for OpenClaw

> **Audience**: AI agents (OpenClaw, Claude Code, Codex, Cursor, or any
> agentic framework that can execute shell commands and read files).
> Written in plain, imperative language that all LLM-based agents can
> parse and execute without ambiguity.

---

## Prerequisites

Before you begin, confirm the following:

- **Python 3.11+** is available on the system.
- **Git** is installed and functional.
- At least one LLM provider API key is available (see Provider table below).

### Supported LLM Providers

| Provider | Env Variable | Example Models |
|----------|-------------|----------------|
| Anthropic | `ANTHROPIC_API_KEY` | `claude-opus-4-20250514`, `claude-sonnet-4-20250514`, `claude-haiku-4-20250414` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o`, `gpt-4.1`, `o3`, `o4-mini` |
| Google Gemini | `GEMINI_API_KEY` | `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.0-flash` |
| GLM (Zhipu AI) | `GLM_API_KEY` | `glm-4-plus`, `glm-4-air`, `glm-4-flash` |
| Kimi (Moonshot) | `KIMI_API_KEY` | `moonshot-v1-8k`, `kimi-latest` |
| Custom endpoint | `CUSTOM_LLM_API_KEY` + `CUSTOM_LLM_BASE_URL` | Any OpenAI-compatible model |

Provider detection is automatic. The model name prefix determines the
provider: `claude-` → Anthropic, `gpt-`/`o3`/`o4` → OpenAI,
`gemini-` → Gemini, `glm-` → GLM, `moonshot-`/`kimi-` → Kimi.
Unknown prefixes route to the custom endpoint if configured, otherwise
Anthropic.

---

## Step 1 — Clone and Install

### 1.1 Clone the repository

```bash
git clone https://github.com/Fei2-Labs/Kompany.git
cd Kompany/kompany
```

### 1.2 Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 1.3 Install the package with all extras

```bash
pip install -e ".[api,mcp,dev]"
```

### 1.4 Verify installation

```bash
kompany --help
```

**Expected output**: A list of 8 commands: `init`, `directive`, `status`,
`projects`, `project`, `debate`, `ledger`, `execute`.

**If this fails**, see [Debug: Installation](#debug-installation).

---

## Step 2 — Configure API Keys

Set at least one provider API key. Anthropic is the default provider.

### Option A — Environment variables (recommended)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

To use additional providers:

```bash
export OPENAI_API_KEY="sk-..."
export GEMINI_API_KEY="..."
export GLM_API_KEY="..."
export KIMI_API_KEY="..."
```

### Option B — `.env` file

Create a file at `Kompany/kompany/.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

### Option C — Custom OpenAI-compatible endpoint

```bash
export CUSTOM_LLM_BASE_URL="https://my-endpoint.example.com/v1"
export CUSTOM_LLM_API_KEY="my-key"
```

### 2.1 Verify the key is set

```bash
python3 -c "import os; k=os.environ.get('ANTHROPIC_API_KEY',''); print('OK' if k else 'MISSING')"
```

**Expected output**: `OK`

**If this fails**, see [Debug: API Key](#debug-api-key).

---

## Step 3 — Initialize a Company

```bash
kompany init --name "Demo Corp" --capital 100 --goal "AI-powered analytics" --time-horizon "6 months" --exclusions "none"
```

### Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `--name` | Company name | `"Demo Corp"` |
| `--capital` | Starting capital in EUR | `100` |
| `--goal` | One-line company goal / product description | `"AI-powered analytics"` |
| `--time-horizon` | Planning time horizon | `"6 months"`, `"1 year"` |
| `--exclusions` | Domains or activities to exclude | `"none"`, `"gambling, weapons"` |

### 3.1 Verify initialization

```bash
kompany status
```

**Expected output**: Company name, balance of €100.00, zero AI costs,
zero active projects.

**If this fails**, see [Debug: Initialization](#debug-initialization).

---

## Step 4 — Send an Informational Directive (No AI Cost)

Start with a zero-cost directive to confirm the pipeline works end to end.

```bash
kompany directive "What's our current balance?"
```

**Expected behavior**:
- Directive type: INFORMATIONAL
- Response includes the balance (€100.00)
- AI cost: $0.00 (informational directives are mechanical, no LLM call)
- The ledger is unchanged

### 4.1 Verify

```bash
kompany ledger --limit 5
```

**Expected output**: Only the initial capital entry. No AI cost entries.

---

## Step 5 — Send a Strategic Directive (Low AI Cost)

```bash
kompany directive "Should we focus on B2B or B2C for our first 10 customers?"
```

**Expected behavior**:
- Directive type: STRATEGIC
- CEO provides strategic analysis with a recommendation
- AI cost: ~$0.03–$0.10 (one Opus or Sonnet call)
- The ledger now has an `ai_cost` entry

### 5.1 Verify cost tracking

```bash
kompany status
```

**Expected output**: Balance is now slightly below €100.00 (AI cost
deducted). The AI cost is shown separately.

---

## Step 6 — Send an Acquisition Directive (Revenue Project)

This is Kompany's signature behavior: mission integrity under budget
constraints.

```bash
kompany directive "Buy a Mac Studio M4 128GB, budget €50"
```

**Expected behavior**:
1. CEO classifies as ACQUISITION, estimates ~€4,500 cost
2. CFO checks budget → balance ~€99.90, shortfall ~€4,400
3. CEO creates a **revenue project** with concrete revenue paths
4. Revenue paths include freelance work, consulting, product sales, etc.
5. AI cost: ~$0.15–$0.30 (CEO classify + CEO revenue plan)
6. The mission is **never downgraded** — the response is "here's how
   we'll fund it", not "we can't afford it"

### 6.1 Verify the revenue project was created

```bash
kompany projects
```

**Expected output**: One active revenue project with a name like
`Fund: Buy a Mac Studio M4 128GB`.

### 6.2 View project details

```bash
kompany project <PROJECT_ID>
```

Replace `<PROJECT_ID>` with the ID shown in `kompany projects` output.

**Expected output**: Revenue paths, assigned agents, target amount,
funded amount, plan details.

---

## Step 7 — Run a Multi-Agent Debate

```bash
kompany debate "Should we build SSO or focus on self-serve onboarding?"
```

**Expected behavior**:
1. Multiple agents participate (depends on stage; `solo` uses CEO, CTO,
   CPO, CFO, CoS)
2. Round 1: Independent positions from each agent's domain
3. Round 2: Rebuttal and challenge
4. CoS synthesis: Structured CEO brief with consensus and tensions
5. CEO decision: Final call with rationale, confidence score, next steps
6. AI cost: ~$0.30–$0.50 for solo stage

### 7.1 Verify

```bash
kompany status
```

**Expected output**: Balance reduced by the debate's AI cost. Multiple
`ai_cost` entries in the ledger.

---

## Step 8 — View Full Ledger

```bash
kompany ledger --limit 20
```

**Expected output**: A chronological list of all transactions:
- `income`: Initial capital (€100.00)
- `ai_cost`: Each LLM call as a negative entry (deducted from balance)
- Each entry shows: timestamp, amount, balance-after, description, category

---

## Step 9 — Run the Test Suite

```bash
python -m pytest tests/ -v
```

**Expected output**: All tests pass.

```
============================= tests passed in ~2s ==============================
```

**If tests fail**, see [Debug: Tests](#debug-tests).

---

## Step 10 — (Optional) Override Model Tiers

To use non-Anthropic models for any tier, create or edit
`kompany.yaml` in the `kompany/` directory:

```yaml
company:
  name: "Demo Corp"
  capital: 100
  goal: "AI-powered analytics"
  time_horizon: "6 months"
  exclusions: "none"

models:
  apex: "gpt-4o"
  primary: "gemini-2.5-flash"
  economy: "glm-4-flash"
```

Then set the corresponding API keys:

```bash
export OPENAI_API_KEY="sk-..."
export GEMINI_API_KEY="..."
export GLM_API_KEY="..."
```

Run a directive to verify the new models are used:

```bash
kompany directive "What stage is our company at?"
```

### 10.1 Using a custom endpoint

```yaml
custom_llm:
  base_url: "https://my-llm-proxy.example.com/v1"
  api_key: "my-key"

models:
  primary: "my-custom-model-name"
```

Unknown model prefixes automatically route to the custom endpoint when
`CUSTOM_LLM_BASE_URL` is configured.

---

## Step 11 — (Optional) REST API Mode

Start the API server:

```bash
pip install -e ".[api]"
WEB_DASHBOARD_TOKEN=<strong-token> uvicorn kompany.interfaces.api:app --host 0.0.0.0 --port 8000   # token required off-loopback; send Authorization: Bearer <token>
```

Test with curl:

```bash
# Initialize
curl -X POST http://localhost:8000/init \
  -H "Content-Type: application/json" \
  -d '{"name": "Demo Corp", "capital": 100, "goal": "AI analytics", "time_horizon": "6 months", "exclusions": "none"}'

# Send directive
curl -X POST http://localhost:8000/directive \
  -H "Content-Type: application/json" \
  -d '{"text": "What is our current balance?"}'

# Check status
curl http://localhost:8000/status
```

Interactive API docs available at `http://localhost:8000/docs`.

---

## Step 12 — (Optional) MCP Server Mode

For Claude Code, Cursor, or any MCP-compatible client:

```bash
pip install -e ".[mcp]"
kompany-mcp
```

Add to your MCP client configuration:

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

Available MCP tools: `kompany_init`, `kompany_directive`,
`kompany_status`, `kompany_projects`, `kompany_project`,
`kompany_ledger`, `kompany_debate`, `kompany_execute`.

---

## OpenClaw-Specific Integration

### Workspace Skill Setup

To install Kompany as an OpenClaw skill, copy the skill folder:

```bash
cp -r .claude/skills/kompany ~/.openclaw/skills/kompany
```

Or reference it in your OpenClaw workspace:

```bash
# In your OpenClaw chat
install skill from github.com/Fei2-Labs/Kompany
```

### Agent Configuration

In your OpenClaw `AGENTS.md` or workspace config, register Kompany as
a shell tool:

```yaml
tools:
  - type: shell
    commands:
      - "cd Kompany/kompany && source .venv/bin/activate && kompany directive \"{input}\""
      - "cd Kompany/kompany && source .venv/bin/activate && kompany status"
      - "cd Kompany/kompany && source .venv/bin/activate && kompany projects"
      - "cd Kompany/kompany && source .venv/bin/activate && kompany debate \"{input}\""
      - "cd Kompany/kompany && source .venv/bin/activate && kompany ledger"
```

### Python SDK Alternative

If your OpenClaw agent has Python access:

```python
from kompany import Kompany

k = Kompany()
k.init(name="Demo Corp", capital=100, goal="AI analytics", time_horizon="6 months", exclusions="none")
result = k.directive("Buy a Mac Studio M4 128GB, budget €50")
print(result["message"])
print(f"AI cost: ${result['total_ai_cost']:.4f}")
```

---

## Debug Guide

### Debug: Installation

**Symptom**: `kompany --help` returns "command not found"

```bash
# Check Python version
python3 --version
# Must be 3.11 or higher

# Check venv is activated
which python3
# Should point to .venv/bin/python3

# Reinstall
cd Kompany/kompany
source .venv/bin/activate
pip install -e ".[dev]"

# Retry
kompany --help
```

**Symptom**: `pip install` fails with dependency errors

```bash
# Upgrade pip first
pip install --upgrade pip

# Install with verbose output
pip install -e ".[dev]" -v
```

**Symptom**: `ModuleNotFoundError: No module named 'kompany'`

```bash
# Verify you are in the correct directory
pwd
# Should end with /Kompany/kompany

# Verify the package is installed
pip list | grep kompany
# Should show: kompany 0.1.0

# If not listed, reinstall
pip install -e .
```

---

### Debug: API Key

**Symptom**: `AuthenticationError` or "API key not found"

```bash
# Check if the key is set
echo $ANTHROPIC_API_KEY
# Should print the key (not empty)

# If empty, set it
export ANTHROPIC_API_KEY="sk-ant-..."

# Or check .env file exists
cat Kompany/kompany/.env
# Should contain: ANTHROPIC_API_KEY=sk-ant-...
```

**Symptom**: Wrong provider key for the model being used

```bash
# If using gpt-4o, you need OPENAI_API_KEY (not ANTHROPIC_API_KEY)
export OPENAI_API_KEY="sk-..."

# If using gemini-2.5-pro, you need GEMINI_API_KEY
export GEMINI_API_KEY="..."

# Verify which model is configured
python3 -c "
from kompany.config.settings import KompanySettings
s = KompanySettings.load()
print(f'apex:    {s.model_apex}')
print(f'primary: {s.model_primary}')
print(f'economy: {s.model_economy}')
"
```

**Symptom**: Custom endpoint not being used

```bash
# Verify both env vars are set
echo $CUSTOM_LLM_BASE_URL
echo $CUSTOM_LLM_API_KEY

# Both must be non-empty for custom routing to work
# The model name must NOT match any known prefix (claude-, gpt-, gemini-, etc.)
```

---

### Debug: Initialization

**Symptom**: `kompany status` shows empty company or errors

```bash
# Reinitialize
kompany init --name "Demo Corp" --capital 100 --goal "AI analytics" --time-horizon "6 months" --exclusions "none"

# Check the database exists
ls -la ~/.kompany/
# Should contain kompany.db

# If using a custom data dir, check KOMPANY_DATA_DIR
echo $KOMPANY_DATA_DIR
```

**Symptom**: "Company not initialized" when sending directives

```bash
# The init command must run before any directive
kompany init --name "Demo Corp" --capital 100 --goal "AI analytics" --time-horizon "6 months" --exclusions "none"

# Then verify
kompany status
```

---

### Debug: Tests

**Symptom**: Tests fail with import errors

```bash
# Ensure dev dependencies are installed
pip install -e ".[dev]"

# Run from the correct directory
cd Kompany/kompany
python -m pytest tests/ -v
```

**Symptom**: Specific test failures

```bash
# Run a single test file for isolation
python -m pytest tests/test_providers.py -v
python -m pytest tests/test_multi_provider_client.py -v
python -m pytest tests/test_cost_tracker.py -v
python -m pytest tests/test_engine.py -v

# Run with full traceback
python -m pytest tests/ -v --tb=long
```

**Symptom**: Some tests pass but new provider tests fail

```bash
# Ensure you have the latest code
git pull origin main

# Reinstall (the openai dependency was added)
pip install -e ".[dev]"

# Run again
python -m pytest tests/ -v
# Expected: all passed
```

---

### Debug: LLM Calls

**Symptom**: `openai.AuthenticationError` when using Gemini/GLM/Kimi

```bash
# These providers use the OpenAI SDK with a different base_url
# Verify the correct key is set for the detected provider

# Check which provider a model routes to
python3 -c "
from kompany.llm.providers import detect_provider
print(detect_provider('gemini-2.5-pro'))   # Should print: Provider.GEMINI
print(detect_provider('glm-4-air'))        # Should print: Provider.GLM
print(detect_provider('moonshot-v1-8k'))   # Should print: Provider.KIMI
print(detect_provider('my-custom-model'))  # Should print: None
"
```

**Symptom**: `Connection refused` or timeout errors

```bash
# For custom endpoints, verify the URL is reachable
curl -s -o /dev/null -w "%{http_code}" $CUSTOM_LLM_BASE_URL/models
# Should return 200

# For Gemini, the base URL is:
# https://generativelanguage.googleapis.com/v1beta/openai/

# For GLM (Zhipu AI):
# https://open.bigmodel.cn/api/paas/v4/

# For Kimi (Moonshot):
# https://api.moonshot.cn/v1
```

**Symptom**: Cost tracking shows $0.00 for a new model

```bash
# If the model is not in the pricing table, prefix-based fallback is used.
# If the prefix is also unknown, Sonnet-tier pricing ($3/$15 per M tokens) is used.
# This is expected behavior — costs are estimated, not exact.

python3 -c "
from kompany.llm.models import estimate_cost
print(f'gpt-4o:      \${estimate_cost(\"gpt-4o\", 1000, 500):.6f}')
print(f'gemini-2.5:  \${estimate_cost(\"gemini-2.5-pro\", 1000, 500):.6f}')
print(f'unknown:     \${estimate_cost(\"my-model\", 1000, 500):.6f}')
"
```

---

## Full Demo Script (Copy-Paste)

Run this entire block to execute the complete demo from scratch:

```bash
# Clone and install
git clone https://github.com/Fei2-Labs/Kompany.git
cd Kompany/kompany
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Set API key (replace with your actual key)
export ANTHROPIC_API_KEY="sk-ant-YOUR-KEY-HERE"

# Verify
kompany --help

# Initialize company
kompany init --name "Demo Corp" --capital 100 --goal "AI-powered analytics" --time-horizon "6 months" --exclusions "none"

# Informational directive (free)
kompany directive "What's our current balance?"

# Strategic directive
kompany directive "Should we focus on B2B or B2C?"

# Acquisition directive (triggers revenue project)
kompany directive "Buy a Mac Studio M4 128GB, budget €50"

# View results
kompany status
kompany projects
kompany ledger --limit 10

# Multi-agent debate
kompany debate "Should we build SSO or focus on self-serve onboarding?"

# Final status
kompany status
kompany ledger --limit 20

# Run tests
python -m pytest tests/ -v
```

---

## Summary

| Step | Command | Verifies |
|------|---------|----------|
| Install | `pip install -e ".[dev]"` | Package and dependencies |
| API key | `export ANTHROPIC_API_KEY=...` | LLM provider access |
| Init | `kompany init --name ... --capital 100 --goal ...` | Database and config |
| Info directive | `kompany directive "What's our balance?"` | Zero-cost pipeline |
| Strategic directive | `kompany directive "Should we focus on B2B?"` | LLM call + cost tracking |
| Acquisition directive | `kompany directive "Buy a Mac Studio..."` | Mission integrity + revenue project |
| Debate | `kompany debate "SSO vs onboarding?"` | Multi-agent protocol |
| Ledger | `kompany ledger --limit 20` | Full financial transparency |
| Tests | `python -m pytest tests/ -v` | All tests pass |

All interfaces (CLI, REST API, MCP, Python SDK) call the same
`KompanyEngine` — same logic, same ledger, same results.
