# OpenClaw Integration Guide

This guide walks you through using Kompany with [OpenClaw](https://github.com/openclaw/openclaw) and other multi-agent platforms. Kompany is a standalone Python package — any tool that can run shell commands or make HTTP requests can use it.

## Table of Contents

1. [Overview](#overview)
2. [Integration Modes](#integration-modes)
3. [CLI Integration (Any Platform)](#cli-integration-any-platform)
4. [Python SDK Integration](#python-sdk-integration)
5. [REST API Integration](#rest-api-integration)
6. [MCP Server Integration](#mcp-server-integration)
7. [OpenClaw Native Setup](#openclaw-native-setup)
8. [Codex / Other Agent Platforms](#codex--other-agent-platforms)
9. [Squad Architecture](#squad-architecture)
10. [Agent Identity System](#agent-identity-system)

---

## Overview

Kompany is designed to work with any AI agent platform. All interfaces call the same `KompanyEngine` — same logic, same ledger, same results.

| Interface | Best For |
|---|---|
| **CLI** | Any platform with shell access (OpenClaw, Codex, Claude Code) |
| **Python SDK** | Platforms with Python runtime (custom agents, scripts) |
| **REST API** | Any HTTP client (web apps, microservices, webhooks) |
| **MCP Server** | MCP-compatible clients (Claude Code, Cursor) |

---

## Integration Modes

### Mode 1: CLI (Simplest)

Any platform that can run shell commands can use Kompany immediately:

```bash
kompany directive "Buy a Mac Studio M4 128GB, budget €50"
```

### Mode 2: Python SDK (Programmatic)

For platforms with Python access:

```python
from kompany import Kompany
k = Kompany()
result = k.directive("Buy a Mac Studio M4 128GB, budget €50")
```

### Mode 3: REST API (HTTP)

For any HTTP client:

```bash
curl -X POST http://localhost:8000/directive \
  -H "Content-Type: application/json" \
  -d '{"text": "Buy a Mac Studio M4 128GB, budget €50"}'
```

### Mode 4: MCP Server (Claude Ecosystem)

For Claude Code, Cursor, and other MCP clients:

```bash
kompany-mcp
```

---

## CLI Integration (Any Platform)

### Prerequisites

```bash
cd kompany
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Available Commands

```bash
kompany init --name "Acme" --product "AI tools" --balance 50 --stage solo
kompany directive "Your directive here"
kompany status
kompany projects
kompany project <project_id>
kompany debate "Strategic question here"
kompany ledger --limit 10
kompany execute <project_id>
```

### Example: OpenClaw Agent Using CLI

In your OpenClaw agent config, use Kompany via shell tool:

```yaml
# openclaw agent config
tools:
  - type: shell
    commands:
      - "kompany directive \"{input}\""
      - "kompany status"
      - "kompany projects"
```

---

## Python SDK Integration

### Installation

```bash
pip install -e ./kompany
```

### Full Example

```python
from kompany import Kompany

# Create and initialize
k = Kompany()
k.init(name="Acme SaaS", product="AI invoice tools", balance=50, stage="solo")

# Send directives
result = k.directive("Buy a Mac Studio M4 128GB, budget €50")
print(f"Status: {result['status']}")
print(f"Message: {result['message']}")
print(f"AI cost: ${result['total_ai_cost']:.2f}")

# Check state
print(f"Balance: €{k.balance():.2f}")
print(f"Active projects: {len(k.projects())}")

# View financials
for entry in k.ledger(limit=5):
    print(f"  {entry['category']}: {entry['description']} ({entry['amount']})")

# Execute a project
projects = k.projects()
if projects:
    result = k.execute_project(projects[0]["id"])
    print(f"Execution result: {result}")
```

### SDK Methods

| Method | Returns | Description |
|---|---|---|
| `Kompany(config_path=None)` | — | Constructor |
| `init(name, product, balance, stage)` | `None` | Initialize company |
| `directive(text)` | `dict` | Send a directive |
| `status()` | `dict` | Company status |
| `projects()` | `list[dict]` | Active projects |
| `project(id)` | `dict \| None` | Project details |
| `balance()` | `float` | Current balance |
| `ledger(limit=10)` | `list[dict]` | Ledger entries |
| `execute_project(id)` | `dict` | Execute project tasks |

---

## REST API Integration

### Start the Server

```bash
pip install -e ".[api]"
uvicorn kompany.interfaces.api:app --host 0.0.0.0 --port 8000
```

### Endpoints

| Method | Endpoint | Body | Description |
|---|---|---|---|
| POST | `/init` | `{name, product, balance, stage}` | Initialize company |
| POST | `/directive` | `{text}` | Send directive |
| GET | `/status` | — | Company status |
| GET | `/projects` | — | List projects |
| GET | `/projects/{id}` | — | Project details |
| GET | `/ledger?limit=N` | — | Ledger entries |
| POST | `/projects/{id}/execute` | — | Execute project |

### Interactive Docs

Once running, visit `http://localhost:8000/docs` for the auto-generated Swagger UI.

---

## MCP Server Integration

### Start the MCP Server

```bash
pip install -e ".[mcp]"
kompany-mcp
```

### Claude Code Configuration

Add to your Claude Code MCP settings (`.claude/settings.json` or project settings):

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

### Available MCP Tools

| Tool | Parameters | Description |
|---|---|---|
| `kompany_init` | `name`\*, `product`\*, `balance`, `stage` | Initialize company |
| `kompany_directive` | `text`\* | Send directive |
| `kompany_status` | — | Company status |
| `kompany_projects` | — | List projects |
| `kompany_project` | `project_id`\* | Project details |
| `kompany_ledger` | `limit` | Ledger entries |
| `kompany_debate` | `question`\* | Run debate |
| `kompany_execute` | `project_id`\* | Execute project |

---

## OpenClaw Native Setup

For full OpenClaw integration where each Kompany agent runs as a standalone OpenClaw agent:

### 1. Initialize OpenClaw Project

```bash
openclaw init kompany
cd kompany
```

### 2. Configure Agent Routing

Each C-suite agent can be registered as an OpenClaw agent. The CLI serves as the bridge:

```json
{
  "name": "kompany",
  "version": "2.0.0",
  "description": "Autonomous business OS — Kompany",
  "agents": ["ceo", "cto", "cpo", "cfo", "cmo", "cro", "coo", "csa", "ciso", "cos", "cv"],
  "tools": {
    "shell": {
      "commands": [
        "kompany directive \"{input}\"",
        "kompany debate \"{input}\"",
        "kompany status",
        "kompany projects",
        "kompany ledger"
      ]
    },
    "agentToAgent": {
      "enabled": true,
      "maxRecursion": 3
    }
  }
}
```

### 3. Communication Rules

- **Intra-squad**: Agents in the same squad communicate directly
- **Cross-squad**: Messages route through CoS (Chief of Staff)
- **Squad leads** always participate in debates; other members join when relevant

---

## Codex / Other Agent Platforms

Any platform with shell access can use Kompany:

```bash
# Codex / generic agent
kompany directive "Your directive here"
```

The CLI returns structured output that any agent can parse. All state is persisted in SQLite, so multiple sessions share the same company state.

---

## Squad Architecture

Kompany agents are organized into three squads:

| Squad | Mission | Members |
|---|---|---|
| **Strategy** | Strategic direction & financial health | CEO, CFO, COO, CoS |
| **Product** | Product-market fit & technical delivery | CTO, CPO, CSA, CISO |
| **Growth** | Revenue & market expansion | CMO, CRO, CV |

### Communication Model

- **Intra-squad**: Direct agent-to-agent communication
- **Cross-squad**: Mediated through the Chief of Staff (CoS)
- **Squad leads**: CFO (Strategy), CPO (Product), CRO (Growth)

---

## Agent Identity System

Each agent's personality is defined by a `soul.yaml` file in `kompany/src/kompany/agents/souls/`. These YAML files contain:

- **Name and role** — What the agent does
- **Domain expertise** — Areas of knowledge
- **Optimization objectives** — What the agent prioritizes
- **Personality traits** — How the agent communicates and debates
- **Relationships** — Which agents they collaborate or clash with

### Agent Memory

Each agent has persistent memory stored in SQLite (via `AgentMemory`):

- Learnings from past directives
- Positions taken in debates
- Company-specific knowledge accumulated over time

Memory is scoped per-agent and persists across sessions.

### Customizing Agents

To modify an agent's personality, edit their soul file:

```
kompany/src/kompany/agents/souls/cto.yaml
```

The system prompt is assembled at runtime from: `soul.yaml` personality + company context + directive context.
