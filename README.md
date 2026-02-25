# AI C-Suite Multi-Agent Framework

A multi-agent orchestration system that simulates an executive leadership team for SaaS solo founders and SMEs. Nine AI executives debate strategic decisions through structured rounds, surface tradeoffs, and deliver actionable recommendations — so you get the benefit of a full C-suite without the payroll.

## Why This Exists

Solo founders make high-stakes decisions daily across product, engineering, finance, marketing, sales, and operations — often without anyone to challenge their thinking. Single-agent AI tools answer questions in isolation. They don't debate, push back, or synthesize cross-functional perspectives.

This framework simulates the dynamic of a real leadership team: informed disagreement, structured debate, and final executive judgment.

## How It Works

```
Topic → Data Layer (CV + CFO gather evidence)
      → Round 1: Independent positions (each exec argues from their domain)
      → Round 2: Rebuttal & challenge (execs respond to each other)
      → Round 3: Convergence (move toward consensus, flag hard lines)
      → CoS Synthesis (structured CEO brief)
      → CEO Decision (final call with rationale + next steps)
```

## The Executive Team

| Agent | Role | Optimizes For |
|---|---|---|
| **CEO** | Final decision-maker | Vision, strategy, tie-breaking |
| **CTO** | Technology leadership | Technical correctness, scalability |
| **CPO** | Product leadership | User value, time-to-market, PMF |
| **CFO** | Financial leadership | Runway, unit economics, ROI |
| **CMO** | Marketing leadership | Brand equity, top-of-funnel growth |
| **CRO** | Revenue leadership | Pipeline, deal velocity, ARR |
| **COO** | Operations leadership | Execution capacity, process reliability |
| **CSA** | Solution architecture | Architectural integrity, integrations |
| **CISO** | Security & compliance | Risk mitigation, compliance posture |
| **CoS** | Debate moderator | Neutral facilitator, synthesizer |
| **CV** | Customer voice | Grounds debate in real customer data |

## Key Features

- **Squad Architecture** — Agents organized into Strategy, Product, and Growth squads (Spotify model). Intra-squad agents communicate directly; cross-squad goes through CoS.
- **Three-File Identity** — Each agent has `SOUL.md` (identity), `USER.md` (org context), `MEMORY.md` (persistent learning across sessions).
- **Tiered Model Strategy** — Opus 4.6 for CEO decisions, Sonnet 4.6 for debate agents, Haiku 4 for fallback/economy mode.
- **Cost Management** — Hard ceiling per debate ($2 max full board, $0.50 solo mode), automatic model fallback on budget.
- **Solo Mode** — Reduced agent roster and rounds for bootstrapped founders watching every dollar.
- **Human-in-the-Loop** — Intervene before, during, or after debates. Redirect, constrain, inject context, or override.
- **Guardrails** — Input validation, output schema enforcement, and tripwire detection on every agent call.
- **Decision Journal** — Every decision logged with rationale, tradeoffs, and outcome tracking.
- **Memory System** — Short-term (in-debate), entity memory (cross-debate, SQLite), long-term (future: vector embeddings).

## Quick Start

### Prerequisites

- Python 3.11+
- Anthropic API key

### Installation

```bash
git clone https://github.com/your-username/ai-csuite-framework.git
cd ai-csuite-framework
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

1. Copy the example company config:
```bash
cp config/company.example.yaml config/company.yaml
```

2. Edit `config/company.yaml` with your company details:
```yaml
name: "Your Company"
product: "One-line product description"
stage: "solo"          # solo | pre-seed | seed | series-a
arr: "$0"
runway_months: 18
team_size: 1
constraints:
  - "bootstrapped"
  - "solo developer"
```

3. Set your API key:
```bash
export ANTHROPIC_API_KEY=your-key-here
```

### Run a Decision

```bash
python3 main.py "Should we build SSO or focus on self-serve onboarding?"
```

## Stage Profiles

The framework adapts its agent roster and cost profile based on your company stage:

| Stage | Active Agents | Rounds | Max Cost |
|---|---|---|---|
| Solo founder | CEO, CTO, CPO, CFO, CoS | 2 | ~$0.50 |
| Pre-seed | CEO, CTO, CPO, CoS, CV | 2 | ~$0.75 |
| Seed | CEO, CTO, CPO, CMO, CRO, CoS, CV | 3 | ~$1.50 |
| Series A | All 11 agents | 3 | ~$2.00 |

## Project Structure

```
ai-csuite/
├── agents/                # Agent definitions (SOUL/USER/MEMORY + logic)
│   ├── base.py            # BaseAgent class
│   ├── ceo/               # CEO agent (Opus 4.6)
│   ├── cto/               # CTO agent
│   ├── ...                # One directory per agent
├── core/                  # Orchestration engine
│   ├── debate.py          # Debate loop orchestrator
│   ├── squads.py          # Squad definitions and routing
│   ├── comms.py           # Agent-to-agent communication
│   ├── guardrails.py      # Input/output validation
│   ├── cost_tracker.py    # Cost accounting
│   └── schemas.py         # Pydantic models
├── memory/                # Persistence layer
│   ├── entity.py          # Entity memory (SQLite)
│   └── journal.py         # Decision journal
├── config/                # YAML configuration
│   ├── company.yaml       # Your company context
│   ├── profiles.yaml      # Stage profiles
│   ├── squads.yaml        # Squad membership
│   └── costs.yaml         # Model pricing + budgets
├── eval/                  # Evaluation and testing
├── tracing/               # Observability
├── logs/                  # Debate logs (JSON)
├── main.py                # CLI entry point
└── .claude/skills/ai-csuite/SKILL.md  # Claude Code / OpenClaw skill
```

## Using as a Claude Code Skill

This framework ships as a Claude Code skill. Invoke it directly:

```
/ai-csuite "Should we raise a Series A or extend runway?"
```

See [docs/USAGE_GUIDE.md](docs/USAGE_GUIDE.md) for detailed usage instructions.

## OpenClaw Integration

This framework is fully compatible with OpenClaw's multi-agent system. Each C-Suite agent can run as a standalone OpenClaw agent with its own messaging channel.

See [docs/OPENCLAW_INTEGRATION.md](docs/OPENCLAW_INTEGRATION.md) for step-by-step setup.

## Documentation

- [Usage Guide](docs/USAGE_GUIDE.md) — Detailed instructions for running debates, customizing agents, and best practices
- [OpenClaw Integration](docs/OPENCLAW_INTEGRATION.md) — How to deploy as an OpenClaw multi-agent system
- [PRD](ai-csuite-framework-prd.md) — Full product requirements document (v1.2)
- [Changelog](CHANGELOG.md) — Version history

## License

MIT — see [LICENSE](LICENSE) for details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
