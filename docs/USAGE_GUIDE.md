# AI C-Suite Framework — Usage Guide

This guide covers everything you need to run strategic debates with your AI executive team.

## Table of Contents

1. [Installation](#installation)
2. [Configuration](#configuration)
3. [Running Your First Debate](#running-your-first-debate)
4. [Understanding the Debate Flow](#understanding-the-debate-flow)
5. [Stage Profiles](#stage-profiles)
6. [Solo Mode](#solo-mode)
7. [Customizing Agents](#customizing-agents)
8. [Human-in-the-Loop Interventions](#human-in-the-loop-interventions)
9. [Cost Management](#cost-management)
10. [Memory and Decision Journal](#memory-and-decision-journal)
11. [Using as a Claude Code Skill](#using-as-a-claude-code-skill)
12. [Best Practices](#best-practices)
13. [Troubleshooting](#troubleshooting)

---

## Installation

### Prerequisites

- Python 3.11 or higher
- An Anthropic API key with access to Claude Sonnet 4.6 (minimum) and Opus 4.6 (recommended for CEO agent)
- pip or pipenv for dependency management

### Setup

```bash
# Clone the repository
git clone https://github.com/your-username/ai-csuite-framework.git
cd ai-csuite-framework

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Set your API key
export ANTHROPIC_API_KEY=your-key-here
```

### Verify Installation

```bash
python3 main.py --health-check
```

This confirms API connectivity, model access, and config file validity.

---

## Configuration

### Company Context (`config/company.yaml`)

This is the most important config file. Every agent reads it before taking a position.

```yaml
name: "Acme SaaS"
product: "AI-powered invoice reconciliation for SMBs"
stage: "solo"                    # solo | pre-seed | seed | series-a
arr: "$0"                        # or "$48,000", "$1.2M", etc.
mrr: "$0"
runway_months: 18
team_size: 1
constraints:
  - "bootstrapped"
  - "solo developer"
  - "no hiring budget"
target_market: "US SMBs with 10-50 employees"
key_metrics:
  churn_rate: "5%"
  cac: "$120"
  ltv: "$1,400"
```

If this file doesn't exist when you run a debate, the framework will prompt you to provide the basics interactively.

### Stage Profiles (`config/profiles.yaml`)

Controls which agents participate and how many rounds run based on your company stage:

```yaml
solo:
  agents: [CEO, CTO, CPO, CFO, CoS]
  rounds: 2
  model_overrides:
    CFO: "claude-haiku-4-20250414"    # economy model for solo
  max_cost_usd: 0.50

pre_seed:
  agents: [CEO, CTO, CPO, CoS, CV]
  rounds: 2
  model_overrides:
    CV: "claude-haiku-4-20250414"
  max_cost_usd: 0.75

seed:
  agents: [CEO, CTO, CPO, CMO, CRO, CoS, CV]
  rounds: 3
  max_cost_usd: 1.50

series_a:
  agents: [CEO, CTO, CPO, CMO, CRO, CFO, COO, CSA, CISO, CoS, CV]
  rounds: 3
  max_cost_usd: 2.00
```

### Squad Configuration (`config/squads.yaml`)

Defines which agents belong to which squad and communication rules:

```yaml
squads:
  strategy:
    mission: "Strategic direction & financial health"
    lead: CFO
    members: [CEO, CFO, COO, CoS]
  product:
    mission: "Product-market fit & technical delivery"
    lead: CPO
    members: [CTO, CPO, CSA, CISO]
  growth:
    mission: "Revenue & market expansion"
    lead: CRO
    members: [CMO, CRO, CV]

communication:
  intra_squad: direct          # agents in same squad talk directly
  cross_squad: mediated        # cross-squad goes through CoS
  max_recursion: 3
```

---

## Running Your First Debate

### Basic Usage

```bash
python3 main.py "Should we build SSO or focus on self-serve onboarding?"
```

### With Options

```bash
# Override stage profile
python3 main.py --stage seed "Should we raise a Series A?"

# Solo mode (minimal agents, minimal cost)
python3 main.py --solo "What pricing tier should we add?"

# Verbose output (show full thinking chains)
python3 main.py --verbose "Monolith vs microservices for our API?"

# Save decision to journal
python3 main.py --save "Should we hire a sales rep or invest in PLG?"
```

---

## Understanding the Debate Flow

Every debate follows this sequence:

### Step 1: Data Layer (Pre-Round)

Before any agent takes a position, the data-gathering agents run first:

- **Customer Voice (CV)**: Summarizes relevant customer signals
- **CFO**: Surfaces financial constraints (runway, unit economics, budget)

This grounds the debate in facts, not opinions.

### Step 2: Round 1 — Independent Positions

Each active agent (excluding CoS and CEO) generates their position independently. No agent sees another's position. Each must provide:

- Domain-specific analysis (3-5 sentences)
- A concrete recommendation
- Confidence level (low / medium / high)

### Step 3: Human Checkpoint (Optional)

After Round 1, you're asked: "Want to redirect, add constraints, or continue?"

This is where you can inject context the agents don't have — a competitor move, a customer conversation, a board constraint.

### Step 4: Round 2 — Rebuttal & Challenge

Each agent now sees all Round 1 positions and must:

- Acknowledge valid points from other agents by name
- Challenge specific points they disagree with
- Update their own position if warranted

Intra-squad agents address each other directly. Cross-squad challenges reference the other agent's squad context.

### Step 5: Round 3 — Convergence (3-round mode only)

Agents move toward consensus. They state concessions and any non-negotiable hard lines. Skipped in 2-round mode (solo/pre-seed stages).

### Step 6: CoS Synthesis

The Chief of Staff produces a structured CEO brief covering: consensus position, key tensions, recommended option, risk flags, and the decision required.

### Step 7: CEO Decision

The CEO agent (running on Opus 4.6) reviews everything and makes the final call. The output includes: decision, rationale, tradeoffs weighed, overrides, next steps, confidence score, and reversibility assessment.

### Step 8: Post-Decision

You're asked whether to accept, challenge, or re-run with different constraints.

---

## Stage Profiles

Your company stage determines the debate configuration:

| Stage | What Changes |
|---|---|
| **Solo** | Only 4 agents + CoS. 2 rounds. CFO runs on Haiku for cost. Max $0.50/debate. |
| **Pre-seed** | Adds Customer Voice. 2 rounds. CV runs on Haiku. Max $0.75/debate. |
| **Seed** | Adds CMO and CRO. 3 rounds. All on Sonnet. Max $1.50/debate. |
| **Series A** | Full 11-agent roster. 3 rounds. CEO on Opus. Max $2.00/debate. |

Set your stage in `config/company.yaml` or override per-debate:

```bash
python3 main.py --stage series_a "Should we acquire CompetitorX?"
```

---

## Solo Mode

Solo mode is designed for bootstrapped founders who need to minimize API costs. It activates automatically at the `solo` stage, or you can force it:

```bash
python3 main.py --solo "Should I pivot from B2C to B2B?"
```

What solo mode changes:

- **Primary agents**: CTO, CPO, CFO only (the essential triad)
- **Secondary agents**: CMO, CRO, COO skipped entirely
- **Specialist agents**: CSA, CISO skipped
- **Rounds**: Maximum 2
- **Models**: Haiku 4 for non-critical agents, Sonnet 4.6 for primary
- **CEO**: Still runs on the best available model for the final decision

---

## Customizing Agents

### Editing Agent Identity

Each agent's behavior is controlled by three files in `agents/<role>/`:

**SOUL.md** — Core identity and debate behavior:
```markdown
# CTO — Chief Technology Officer

## Identity
I am the CTO. I optimize for technical correctness, scalability, and engineering velocity.

## Debate Behavior
- I challenge proposals that create technical debt
- I push back on timelines that compromise code quality
- I defer to CPO on user value questions but hold firm on architecture
```

**USER.md** — Organizational context:
```markdown
## Squad
Product Squad (Lead: CPO)

## Relationships
- Reports to: CEO
- Collaborates with: CPO (daily), CSA (architecture reviews)
- Common clashes: CPO (speed vs quality), CFO (infrastructure cost)
```

**MEMORY.md** — Updated automatically after each debate:
```markdown
## Decision History
- 2026-02-20 Pricing debate: Argued for SSO-first. Overruled by CEO (PLG priority).
- 2026-02-22 Architecture debate: Pushed for event-driven. Consensus reached.
```

### Adding Domain-Specific Tools

Agents can be given tools relevant to their domain. Configure in `agents/<role>/agent.py`:

```python
# agents/cfo/agent.py
TOOLS = [
    {"name": "runway_calculator", "description": "Calculate runway from burn rate and cash"},
    {"name": "unit_economics", "description": "Compute LTV, CAC, payback period"},
]
```

### Creating a New Agent

1. Create a directory: `agents/your_role/`
2. Add `SOUL.md`, `USER.md`, `MEMORY.md`
3. Add `agent.py` with role-specific tools
4. Register in `config/squads.yaml` under the appropriate squad
5. Add to the relevant stage profile in `config/profiles.yaml`

---

## Human-in-the-Loop Interventions

The framework is a decision-support tool, not an autopilot. You can intervene at multiple points:

### Pre-Debate
- Set constraints: "Assume we can't hire for 6 months"
- Inject context: "We just lost our biggest customer"
- Adjust roster: "Skip CISO for this one, it's not security-related"

### Mid-Debate (after Round 1)
- **Redirect**: Change the framing of the question
- **Constrain**: Add a new constraint agents must respect
- **Inject**: Provide data or context agents don't have
- **Skip**: Jump straight to CEO decision

### Post-Decision
- **Accept**: Log the decision and move on
- **Challenge**: Push back on the CEO's reasoning
- **Re-run**: Start over with different constraints or roster

---

## Cost Management

### How Costs Work

Every API call to Claude is tracked per-agent, per-round, per-debate. The framework enforces:

- **Per-debate hard ceiling**: $2.00 (full board), $0.50 (solo mode)
- **Per-agent soft limit**: $0.30 — triggers a warning, not a stop
- **Model fallback chain**: If budget is tight, agents automatically fall back from Sonnet → Haiku

### Monitoring Costs

After each debate, a cost summary is printed:

```
┌─────────────────────────────────────┐
│ DEBATE COST SUMMARY                 │
├──────────┬──────────┬───────────────┤
│ Agent    │ Calls    │ Cost (USD)    │
├──────────┼──────────┼───────────────┤
│ CTO      │ 3        │ $0.12         │
│ CPO      │ 3        │ $0.11         │
│ CFO      │ 3        │ $0.08         │
│ CoS      │ 2        │ $0.09         │
│ CEO      │ 1        │ $0.18         │
├──────────┼──────────┼───────────────┤
│ TOTAL    │ 12       │ $0.58         │
└──────────┴──────────┴───────────────┘
```

---

## Memory and Decision Journal

### Three-Tier Memory

1. **Short-term**: The debate context itself — positions, rebuttals, synthesis. Lives only during the debate.
2. **Entity memory**: Cross-debate knowledge stored in SQLite. Tracks agent positions, company facts, recurring themes.
3. **Long-term** (future): Vector embeddings for semantic search across past decisions.

### Decision Journal

Every saved debate creates a `DecisionRecord` in `data/journal.sqlite`:

```bash
# View recent decisions
python3 main.py --journal

# View a specific decision
python3 main.py --journal --id 42

# Search decisions
python3 main.py --journal --search "pricing"
```

Each record includes: topic, all agent positions, CEO decision, rationale, confidence score, reversibility, and timestamp.

---

## Using as a Claude Code Skill

The framework ships as a Claude Code skill in `.claude/skills/ai-csuite/SKILL.md`.

### Invocation

```
/ai-csuite "Should we build SSO or focus on self-serve onboarding?"
```

### What Happens

Claude Code reads the skill definition and simulates the full debate protocol inline — data layer, rounds, synthesis, CEO decision — all within your conversation. No separate process needed.

### Requirements

- The skill file must be at `.claude/skills/ai-csuite/SKILL.md`
- `config/company.yaml` should exist (or you'll be prompted for context)
- The skill uses these tools: Read, Write, Edit, Bash, Glob, Grep, Task, WebSearch, WebFetch

---

## Best Practices

### Framing Good Decision Topics

The quality of the debate depends heavily on how you frame the question.

**Good topics** (specific, bounded, actionable):
- "Should we build SSO or focus on self-serve onboarding for Q2?"
- "Our biggest competitor just raised $50M. What's our response?"
- "Should we raise prices 20% and risk churn, or keep prices and extend runway?"

**Weak topics** (vague, unbounded):
- "How should we grow?" — too broad, agents will give generic advice
- "What should we do?" — no context, no constraints
- "Is our product good?" — not a decision, just a question

### Tips for Better Debates

1. **Add constraints** — "We have $50K budget and 3 months" forces agents to be realistic
2. **Provide context** — The more your `company.yaml` reflects reality, the better the advice
3. **Use the human checkpoint** — After Round 1, inject information agents can't know
4. **Challenge the CEO** — If the decision feels wrong, push back. Re-run with new constraints.
5. **Review MEMORY.md periodically** — Agents learn from past debates. Check that their accumulated knowledge is accurate.

---

## Troubleshooting

### "API key not found"
Set `ANTHROPIC_API_KEY` in your environment or in a `.env` file in the project root.

### "Model not available"
Your API plan may not include Opus 4.6. The framework will fall back to Sonnet 4.6 for the CEO agent. To force this:
```bash
python3 main.py --ceo-model claude-sonnet-4-6-20250620 "Your topic"
```

### "Budget exceeded"
The debate was cut short because it hit the cost ceiling. Options:
- Increase `max_cost_usd` in `config/profiles.yaml`
- Use `--solo` mode for cheaper debates
- Check if an agent is producing unusually long outputs (may indicate a prompt issue)

### "No company context found"
Create `config/company.yaml` with your company details. See the [Configuration](#configuration) section above.

### Agents are too agreeable
If all agents agree in Round 1, the CoS will flag potential groupthink. You can also:
- Make the topic more specific or controversial
- Add constraints that create real tradeoffs
- Edit agent SOUL.md files to sharpen their biases
