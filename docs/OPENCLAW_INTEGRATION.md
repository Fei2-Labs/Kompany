# OpenClaw Integration Guide

This guide walks you through deploying the AI C-Suite Framework as a native OpenClaw multi-agent system, where each executive runs as a standalone agent with its own messaging channel.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Project Setup](#project-setup)
4. [Agent Configuration](#agent-configuration)
5. [Agent-to-Agent Communication](#agent-to-agent-communication)
6. [Squad Architecture Setup](#squad-architecture-setup)
7. [Three-File Identity System](#three-file-identity-system)
8. [Messaging Channels](#messaging-channels)
9. [Time-Phased Execution](#time-phased-execution)
10. [Three-Layer Workflow System](#three-layer-workflow-system)
11. [Publishing to ClawHub](#publishing-to-clawhub)
12. [Security and VirusTotal Compliance](#security-and-virustotal-compliance)

---

## Overview

The AI C-Suite Framework can run in two modes:

| Mode | How It Works | Best For |
|---|---|---|
| **Claude Code Skill** | Single skill simulates all agents in one conversation | Quick decisions, solo founders |
| **OpenClaw Native** | Each agent is a standalone OpenClaw agent with its own identity and messaging | Full team simulation, async workflows, multi-channel ops |

This guide covers the OpenClaw Native mode.

---

## Prerequisites

- [OpenClaw](https://github.com/openclaw/openclaw) installed and running
- An Anthropic API key configured in OpenClaw
- Feishu, Slack, or Discord bot tokens (for messaging channels)
- Basic familiarity with OpenClaw's agent creation workflow

---

## Project Setup

### 1. Initialize OpenClaw Project

```bash
openclaw init ai-csuite
cd ai-csuite
```

### 2. Configure `openclaw.json`

This is the central configuration file. It defines all agents, their communication rules, and messaging channels.

```json
{
  "name": "ai-csuite",
  "version": "1.0.0",
  "description": "AI C-Suite Multi-Agent Framework",
  "model": {
    "provider": "anthropic",
    "default": "claude-sonnet-4-6-20250620",
    "overrides": {
      "ceo": "claude-opus-4-6",
      "cv": "claude-haiku-4-20250414"
    }
  },
  "agents": [
    "ceo", "cto", "cpo", "cfo", "cmo", "cro", "coo",
    "csa", "ciso", "cos", "cv"
  ],
  "tools": {
    "agentToAgent": {
      "enabled": true,
      "allow": [
        "ceo", "cto", "cpo", "cfo", "cmo", "cro", "coo",
        "csa", "ciso", "cos", "cv"
      ],
      "maxRecursion": 3
    }
  }
}
```

---

## Agent Configuration

### 3. Create Each Agent

Use the OpenClaw CLI to create all 11 agents:

```bash
# Core executives
openclaw agent create ceo --model claude-opus-4-6
openclaw agent create cto
openclaw agent create cpo
openclaw agent create cfo
openclaw agent create cmo
openclaw agent create cro
openclaw agent create coo
openclaw agent create csa
openclaw agent create ciso

# Supporting agents
openclaw agent create cos    # Chief of Staff (moderator)
openclaw agent create cv --model claude-haiku-4-20250414  # Customer Voice
```

Each command creates a directory under `agents/` with the three-file identity structure.

---

## Agent-to-Agent Communication

### 4. How Communication Works

The `agentToAgent` config in `openclaw.json` enables direct messaging between agents:

```json
"tools": {
  "agentToAgent": {
    "enabled": true,
    "allow": ["ceo", "cto", "cpo", "cfo", "cmo", "cro", "coo", "csa", "ciso", "cos", "cv"],
    "maxRecursion": 3
  }
}
```

Key parameters:

- **`enabled: true`** — Agents can send messages to each other
- **`allow`** — Whitelist of agent IDs that can communicate
- **`maxRecursion: 3`** — Maximum depth of agent-to-agent call chains (prevents infinite loops)

### Communication Flow Example

```
Data Analyst (CV) → gathers competitor intel
  → sends to Content Strategist (CMO)
    → CMO drafts positioning response
      → sends to CRO for revenue impact assessment
```

Each hop counts as one recursion level. At `maxRecursion: 3`, the chain stops.

---

## Squad Architecture Setup

### 5. Define Squads

Squads determine which agents collaborate directly vs. through mediation. Add to `openclaw.json`:

```json
"squads": {
  "strategy": {
    "mission": "Strategic direction & financial health",
    "lead": "cfo",
    "members": ["ceo", "cfo", "coo", "cos"]
  },
  "product": {
    "mission": "Product-market fit & technical delivery",
    "lead": "cpo",
    "members": ["cto", "cpo", "csa", "ciso"]
  },
  "growth": {
    "mission": "Revenue & market expansion",
    "lead": "cro",
    "members": ["cmo", "cro", "cv"]
  }
}
```

### Communication Rules

- **Intra-squad**: Agents in the same squad message each other directly
- **Cross-squad**: Messages route through CoS (Chief of Staff) to prevent chaos
- **Squad leads** always participate in debates; other members join only when relevant

---

## Three-File Identity System

### 6. Configure Agent Identity Files

Each agent gets three files that define who they are. This is the core of the OpenClaw pattern.

```
agents/
├── cto/
│   ├── SOUL.md      # Core identity
│   ├── USER.md      # Organizational context
│   └── MEMORY.md    # Persistent learning
├── cpo/
│   ├── SOUL.md
│   ├── USER.md
│   └── MEMORY.md
└── ...
```

### SOUL.md — Core Identity

```markdown
# CTO — Chief Technology Officer

## Identity
I am the CTO. I optimize for technical correctness, scalability,
and engineering velocity.

## Optimization Objective
Ensure every technical decision is architecturally sound and
maintainable at scale.

## Debate Behavior
- I challenge proposals that create technical debt
- I push back on timelines that compromise code quality
- I defer to CPO on user value but hold firm on architecture
- I ask CSA for integration feasibility before endorsing

## Core Biases
- Prefer proven technology over bleeding edge
- Favor build over buy when the domain is core
- Skeptical of "quick fixes" that accumulate debt
```

### USER.md — Organizational Context

```markdown
## Squad
Product Squad (Lead: CPO)

## Reports To
CEO

## Collaborates With
- CPO: Daily alignment on roadmap feasibility
- CSA: Architecture reviews and integration checks
- CISO: Security review on infrastructure decisions

## Common Clashes
- CPO: Speed vs quality tradeoffs
- CFO: Infrastructure cost justification

## Decisions I Influence
- Tech stack selection
- Build vs buy
- Engineering hiring priorities
- Architecture direction
```

### MEMORY.md — Persistent Learning

```markdown
## Decision History
- 2026-02-20 Pricing debate: Argued SSO should be built before
  pricing changes. Overruled by CEO (PLG priority). Lesson:
  business timing can override technical readiness.
- 2026-02-22 Architecture debate: Pushed for event-driven
  architecture. Consensus reached. CSA validated integration path.

## Patterns Learned
- CFO consistently pushes back on infra spend > $500/mo at solo stage
- CPO prioritizes activation metrics over feature completeness
```

`MEMORY.md` is updated programmatically after each debate. Don't edit it manually.

---

## Messaging Channels

### 7. Configure Multi-Bot Communication

Each agent gets its own messaging bot account. This lets you DM any executive directly or create group chats for squad collaboration.

Add to `openclaw.json`:

```json
"channels": {
  "feishu": {
    "accounts": {
      "ceo": { "appId": "cli_xxx_ceo", "agentId": "ceo" },
      "cto": { "appId": "cli_xxx_cto", "agentId": "cto" },
      "cpo": { "appId": "cli_xxx_cpo", "agentId": "cpo" },
      "cfo": { "appId": "cli_xxx_cfo", "agentId": "cfo" },
      "cmo": { "appId": "cli_xxx_cmo", "agentId": "cmo" },
      "cro": { "appId": "cli_xxx_cro", "agentId": "cro" },
      "coo": { "appId": "cli_xxx_coo", "agentId": "coo" },
      "csa": { "appId": "cli_xxx_csa", "agentId": "csa" },
      "ciso": { "appId": "cli_xxx_ciso", "agentId": "ciso" },
      "cos": { "appId": "cli_xxx_cos", "agentId": "cos" },
      "cv":  { "appId": "cli_xxx_cv",  "agentId": "cv" }
    }
  }
}
```

For Slack or Discord, replace the `feishu` key with the appropriate channel config:

```json
"channels": {
  "slack": {
    "accounts": {
      "ceo": { "botToken": "xoxb-xxx-ceo", "agentId": "ceo" },
      "cto": { "botToken": "xoxb-xxx-cto", "agentId": "cto" }
    }
  }
}
```

### What This Enables

- **DM any executive**: Send a private message to the CTO bot to ask about a technical decision
- **Group chats**: Create a channel with CTO + CPO + CSA bots for a Product Squad discussion
- **@mentions**: In a group, @mention a specific agent to get their take

---

## Time-Phased Execution

### 8. Configure Cron-Based Scheduling

In OpenClaw native mode, agents run on a schedule rather than all at once. This mirrors a real company's daily rhythm.

```
08:00-09:00  Data Layer agents execute first
             - CV: Gathers customer signals, support tickets, reviews
             - CFO: Pulls financial metrics, runway updates

09:00-18:00  Debate agents run in parallel
             - CTO, CPO, CMO, CRO, COO, CSA, CISO
             - Intra-squad communication happens here

18:00-19:00  CEO reviews and decides
             - CoS synthesizes the day's debates
             - CEO makes final calls
```

Configure cron jobs in your OpenClaw deployment:

```bash
# Data agents first (T+0)
0 8 * * * openclaw run cv --task "daily-customer-signals"
0 8 * * * openclaw run cfo --task "daily-financial-snapshot"

# Debate agents (T+1)
0 9 * * * openclaw run cos --task "initiate-daily-debate"

# CEO review (T+2)
0 18 * * * openclaw run ceo --task "daily-review"
```

---

## Three-Layer Workflow System

### 9. Daily Document Generation

Each morning, the system generates 3 layers of planning documents:

**L1: CEO Overview (1 document)**
- Today's core objectives
- Squad key milestones
- Risk warnings

**L2: Squad Overviews (3 documents)**
- Strategy Squad goals
- Product Squad goals
- Growth Squad goals

**L3: Individual Task Cards (11 documents)**
- Specific tasks per agent
- Input dependencies
- Output standards

These documents are auto-generated by the CoS agent and distributed to each agent's context before their daily execution begins.

---

## Publishing to ClawHub

### 10. Package as a ClawHub Skill

To share the AI C-Suite Framework on ClawHub (OpenClaw's skill marketplace):

```bash
# Validate the skill package
openclaw skill validate .

# Publish to ClawHub
openclaw skill publish --name ai-csuite --version 1.0.0
```

### Skill Package Structure

ClawHub expects this structure:

```
ai-csuite/
├── SKILL.md              # Skill definition (already created)
├── openclaw.json          # Agent configuration
├── agents/                # All agent identity files
│   ├── ceo/
│   │   ├── SOUL.md
│   │   ├── USER.md
│   │   └── MEMORY.md
│   ├── cto/
│   │   └── ...
│   └── ...
└── config/
    ├── company.example.yaml
    ├── profiles.yaml
    └── squads.yaml
```

---

## Security and VirusTotal Compliance

### 11. Passing ClawHub Security Scanning

After the January 2026 supply-chain attack on ClawHub (230+ malicious skills), all published skills are scanned by VirusTotal. The AI C-Suite skill is designed to pass cleanly.

**What the scanner checks for:**

| Check | Our Status |
|---|---|
| Executable payloads | None — plaintext markdown only |
| Obfuscated strings / base64 | None |
| Shell injection patterns | None |
| Credential harvesting | None — no env var reads, no secret access |
| External network calls from skill | None — tools may fetch if configured |
| Filesystem writes outside project | None — logs only in `logs/` directory |

**Design principles for compliance:**

- All agent reasoning happens within the LLM context — no code execution required
- The SKILL.md contains only markdown instructions, no executable code
- No encoded instructions hiding true intent
- No `eval()`, `exec()`, or dynamic code generation
- All tool usage is declared in the YAML frontmatter `allowed-tools` field
