# AI C-Suite Multi-Agent Framework
## Product Requirements Document (PRD)

**Version:** 1.2
**Status:** Draft (Revised — framework comparison + OpenClaw Squad pattern pass)
**Target:** SaaS Startup Intelligence Layer (Solo Founders & SMEs)
**Author:** CEO Agent (Claude)
**Revision Note:** v1.2 incorporates Squad architecture pattern from OpenClaw (阿橙's 16-agent company), three-file identity system, agent-to-agent direct communication, and time-phased execution — on top of v1.1's CrewAI/AutoGen/LangGraph/Agents SDK patterns.

---

## 1. Executive Summary

The AI C-Suite Framework is a multi-agent orchestration system that simulates a functioning executive leadership team for SaaS startups. Each agent operates as a domain expert C-level executive with its own reasoning, tools, and perspective. Agents debate collaboratively, reach consensus, and present unified recommendations to a CEO agent that makes final strategic decisions.

The framework is built from scratch using Anthropic's Claude API with extended thinking enabled, following the single-agent pattern pioneered by Elvis Sun's Coding Agent — adapted for executive decision-making rather than software engineering.

---

## 2. Problem Statement

SaaS startup founders and operators face high-stakes decisions daily across multiple domains — product, engineering, finance, marketing, sales, and operations — often without access to a full executive team. Existing AI tools answer questions in isolation. They don't debate, challenge each other, surface tensions, or synthesize cross-functional perspectives before a decision is made.

This framework fills that gap by simulating the dynamic of a real executive leadership team: informed disagreement, structured debate, and final executive judgment.

---

## 3. Goals & Success Metrics

### Goals
- Provide high-quality, multi-perspective strategic recommendations for SaaS-specific decisions
- Simulate realistic cross-functional debate that surfaces tradeoffs a single agent would miss
- Enable a structured decision workflow that mirrors how strong leadership teams operate
- Be lightweight, transparent, and fully controllable — no black-box orchestration frameworks
- Be cost-conscious by default — a solo founder's AI advisory board, not an enterprise burn machine
- Enable the human founder to intervene at any point — this is a decision-support tool, not an autopilot

### Success Metrics
| Metric | Target |
|---|---|
| Decision coverage across functions | All 8 C-suite domains represented per major decision |
| Debate rounds before consensus | 2–3 rounds average |
| CEO override rate | <30% (high consensus quality) |
| Time to final recommendation | <90 seconds per decision |
| Reasoning traceability | 100% (full thinking chain logged) |
| Cost per decision | < $0.50 average (solo mode), < $2.00 (full board mode) |
| Decision quality score (self-eval) | ≥ 7/10 on structured rubric |
| Founder intervention rate | Tracked per session (no target — data collection) |

---

## 4. Users & Use Cases

### Primary User
Solo founders and micro-teams (1–5 people) running SaaS businesses who need executive-level strategic input without a full leadership team. This is the person who is CEO, CTO, and janitor all at once.

### Secondary Users
- Early-stage to Series A SaaS founders with small teams
- Venture-backed product teams stress-testing roadmap decisions
- Consultants running strategic workshops
- Builders of AI agent frameworks using this as a reference architecture

### Core Use Cases
1. **Product decisions** — Should we build feature X or buy/integrate a third-party solution?
2. **GTM strategy** — What's our go-to-market approach for the enterprise segment?
3. **Pricing** — How should we restructure our pricing tiers?
4. **Hiring** — When should we hire our first Sales VP vs. CRO?
5. **Architecture** — Monolith vs. microservices for our next phase of scale?
6. **Fundraising** — Should we raise a Series A now or extend runway?
7. **Competitive response** — A well-funded competitor just launched. What do we do?

---

## 5. Agent Roster

### 5.1 Core Executive Team

| Agent | Role | Domain Expertise |
|---|---|---|
| **CEO** | Final decision-maker, strategic north star | Vision, investor relations, team alignment, tie-breaking |
| **CTO** | Technology leadership | Tech stack, engineering velocity, build vs. buy, scalability |
| **CPO** | Product leadership | Roadmap, PMF, user stories, feature prioritization, UX |
| **CMO** | Marketing leadership | Demand gen, brand, SEO/content, positioning, CAC |
| **CRO** | Revenue leadership | Pipeline, sales process, pricing, ARR targets, NRR |
| **CFO** | Financial leadership | Runway, unit economics, LTV/CAC, ARR/MRR, fundraising |
| **COO** | Operational leadership | Processes, hiring velocity, cross-team execution |
| **CSA** | Solution architecture | Enterprise architecture, integrations, security reviews, technical feasibility |
| **CISO** | Security & compliance | SOC2, data privacy, enterprise trust, compliance posture |

### 5.2 Supporting Agents

| Agent | Role | Function |
|---|---|---|
| **Chief of Staff (CoS)** | Debate moderator & synthesizer | Facilitates debate rounds, resolves deadlocks, prepares CEO brief |
| **Customer Voice (CV)** | Customer reality check | Trained on support tickets, churn data, NPS, VOC — grounds debate in real customer evidence |

### 5.3 Agent Personality Matrix

Each agent has a defined personality archetype that governs how they argue and what they optimize for.

| Agent | Optimizes For | Typical Stance | Common Clash With |
|---|---|---|---|
| CTO | Technical correctness, scalability | "Is this the right way to build it?" | CPO (speed vs. quality), CFO (cost) |
| CPO | User value, time-to-market | "Is this the right thing to build?" | CTO (feasibility), CRO (revenue priority) |
| CMO | Brand equity, top-of-funnel | "How does this land with the market?" | CRO (brand vs. pipeline) |
| CRO | Revenue, this quarter | "Will this close deals?" | CMO (long-term vs. short-term) |
| CFO | Financial health, unit economics | "Can we afford this? What's the ROI?" | CTO (infra spend), COO (headcount) |
| COO | Execution, operational efficiency | "Can we actually deliver this?" | CPO (scope), CRO (process vs. speed) |
| CSA | Architectural integrity | "Does this integrate cleanly at scale?" | CTO (over-engineering risk) |
| CISO | Risk mitigation, compliance | "What's our exposure here?" | CTO/CPO (velocity vs. security) |

---

## 5.4 Squad Architecture (Inspired by OpenClaw / Spotify Squad Model)

Instead of a flat roster where all agents report to the CEO through CoS, agents are organized into cross-functional Squads. Each Squad is accountable for a business outcome, not just individual tasks. This mirrors the Spotify engineering culture's Squad model, adapted for AI agents.

| Squad | Mission | Members | Accountable For |
|---|---|---|---|
| **Strategy Squad** | Strategic direction & financial health | CEO, CFO, COO, CoS | Runway, unit economics, hiring, OKRs |
| **Product Squad** | Product-market fit & technical delivery | CTO, CPO, CSA, CISO | Roadmap, architecture, build velocity, security |
| **Growth Squad** | Revenue & market expansion | CMO, CRO, CV | Pipeline, CAC, positioning, customer retention |

Why Squads matter:
- Agents within a Squad can communicate directly (see 6.4 Agent-to-Agent Communication) without routing through CoS
- Cross-Squad communication still goes through CoS to prevent chaos
- Each Squad has a lead: CFO leads Strategy, CPO leads Product, CRO leads Growth
- Squad leads participate in every debate; other Squad members join only when relevant

---

## 5.5 Three-File Identity System (Inspired by OpenClaw SOUL/USER/MEMORY Pattern)

Each agent's identity is defined by three persistent files, not just a system prompt. This gives agents stable identity, organizational awareness, and learning capacity.

| File | Purpose | Contents |
|---|---|---|
| `SOUL.md` | Core identity | Role definition, domain expertise, optimization objectives, personality archetype, debate behavior rules, core biases |
| `USER.md` | Organizational context | Which Squad they belong to, who they report to, who they collaborate with, what decisions they influence, their relationship to other agents |
| `MEMORY.md` | Persistent learning | Past positions taken, decisions influenced, patterns learned, company-specific knowledge accumulated across sessions |

```
agents/
├── cto/
│   ├── SOUL.md      # "I am the CTO. I optimize for technical correctness..."
│   ├── USER.md      # "I belong to Product Squad. I report to CEO. I clash with CPO on..."
│   └── MEMORY.md    # "In the pricing debate (2026-02-20), I argued for SSO first..."
```

At agent initialization, all three files are loaded and concatenated into the system prompt. `MEMORY.md` is updated after each debate with key positions taken and outcomes observed.

---

## 6. System Architecture

### 6.1 High-Level Flow

```
┌─────────────────────────────────────────────────────┐
│                   INPUT LAYER                        │
│  Topic / Decision / Question introduced to the team  │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│              ROUTING LAYER (CoS Agent)               │
│  - Determines which agents are relevant              │
│  - Structures the debate topic                       │
│  - Sets the debate agenda                            │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               DEBATE LAYER (Round 1)                 │
│  Each relevant agent responds independently          │
│  through their domain lens                           │
│  Extended thinking enabled per agent                 │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               DEBATE LAYER (Round 2)                 │
│  Agents review other positions and rebut             │
│  Areas of agreement and conflict surfaced            │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               CONSENSUS LAYER (Round 3)              │
│  Agents converge toward shared recommendation        │
│  Dissenting views explicitly flagged                 │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│              SYNTHESIS LAYER (CoS Agent)             │
│  - Summarizes consensus position                     │
│  - Lists unresolved disagreements                    │
│  - Prepares structured brief for CEO                 │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│              DECISION LAYER (CEO Agent)              │
│  - Reviews brief + full debate log                   │
│  - Uses extended thinking to reason through          │
│  - Makes final call with rationale                   │
│  - Assigns ownership to relevant agents              │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│              EXECUTION LAYER (Output)                │
│  - Decision + rationale logged                       │
│  - Action items assigned per agent domain            │
│  - Output delivered to user                          │
└─────────────────────────────────────────────────────┘
```

### 6.2 Agent Architecture (Per Agent)

Each agent follows the Elvis Sun pattern: a think-act-observe loop powered by Claude's extended thinking API.

```python
Agent = {
    "model": "claude-sonnet-4-6-20250620",
    "thinking": {
        "type": "enabled",
        "budget_tokens": 8000  # configurable per agent priority
    },
    "system_prompt": role_system_prompt,   # defines personality, domain, objectives
    "tools": domain_specific_tools,        # e.g., CFO gets financial model tools
    "messages": conversation_history,      # full debate history passed per call
    "output_schema": pydantic_model,       # structured output validation (see 7.5)
    "guardrails": agent_guardrails,        # input/output validation rules (see 8.5)
    "fallback_model": "claude-haiku-4-20250414"  # cheaper fallback on failure/budget
}
```

### 6.3 Data Flow Between Agents

Agents do not call each other directly. All communication is mediated through a shared **debate context object** managed by the orchestrator (CoS agent logic).

```python
DebateContext = {
    "topic": str,                    # The decision or question
    "round": int,                    # Current debate round
    "positions": {                   # Each agent's position per round
        "CTO": [round1, round2],
        "CPO": [round1, round2],
        ...
    },
    "consensus": str | None,         # CoS synthesized consensus
    "dissents": list[str],           # Unresolved disagreements
    "ceo_brief": str,                # Structured brief for CEO
    "final_decision": str | None,    # CEO's final call
    "action_items": dict,            # Per-agent next steps

    # --- Added: Cost & Token Tracking (inspired by LangGraph state) ---
    "token_usage": {                 # Per-agent, per-round token counts
        "CTO": {"round_1": {"input": int, "output": int, "thinking": int}},
        ...
    },
    "total_cost_usd": float,         # Running cost for this debate
    "budget_remaining_usd": float,   # Hard cap minus spent

    # --- Added: Error & Recovery State ---
    "errors": list[dict],            # [{agent, round, error_type, fallback_used}]
    "retries": dict,                 # Per-agent retry count this debate

    # --- Added: Human Intervention Log ---
    "human_interventions": list[dict], # [{round, action, input}]

    # --- Added: Decision Metadata ---
    "confidence_score": float | None,  # CEO self-assessed confidence (1-10)
    "decision_category": str | None,   # e.g., "product", "pricing", "hiring"
    "reversibility": str | None,       # "easily_reversible" | "costly_to_reverse" | "irreversible"
}
```

### 6.4 Agent-to-Agent Direct Communication (Inspired by OpenClaw agentToAgent)

The v1.1 design routed ALL communication through CoS. This is a bottleneck. The OpenClaw pattern allows agents within the same Squad to communicate directly, while cross-Squad communication still goes through CoS.

```python
CommunicationConfig = {
    "intra_squad": {
        "enabled": True,
        "mode": "direct",           # agents in same Squad talk directly
        "max_recursion": 3,         # prevent infinite agent-to-agent loops
    },
    "cross_squad": {
        "enabled": True,
        "mode": "mediated",         # cross-Squad goes through CoS
        "requires_cos_approval": False,  # CoS sees it but doesn't block
    },
}
```

When this matters: In a pricing debate, CRO can directly ask CFO "what's the margin impact of a 20% discount?" without waiting for CoS to relay. But if CRO wants to ask CTO about build effort, that crosses Squads and CoS mediates.

### 6.5 Time-Phased Execution (Inspired by OpenClaw Daily Workflow)

Beyond the debate protocol, the framework supports a daily operational mode where agents execute in phases, not just debate rounds.

```
Phase 1 — Data Layer (runs first, feeds all other agents):
  CV: Collect customer signals (support tickets, NPS, churn data)
  CFO: Pull financial snapshots (runway, burn, MRR changes)

Phase 2 — Analysis Layer (parallel, after Phase 1):
  All debate-participating agents receive Phase 1 data
  Debate rounds execute as defined in Section 9

Phase 3 — Synthesis & Decision:
  CoS synthesis → CEO decision (sequential)

Phase 4 — Action Output:
  Action items distributed per agent/Squad
  Decision record saved to journal
```

This ensures agents debate with fresh data, not stale context.

---

## 7. Detailed Component Specifications

### 7.1 System Prompt Architecture

Each agent's system prompt is assembled from the three-file identity system (see 5.5). At runtime, the prompt is constructed as:

```
system_prompt = SOUL.md + USER.md + MEMORY.md + COMPANY_CONTEXT + ROUND_INSTRUCTIONS
```

The SOUL.md provides the consistent structure:

```
[IDENTITY]
You are the [ROLE] of a SaaS startup. Your name is [NAME].

[DOMAIN EXPERTISE]
Your expertise covers: [LIST OF DOMAINS]

[OPTIMIZATION OBJECTIVE]
You always argue in favor of [PRIMARY OBJECTIVE].
When in doubt, you default to [CORE BIAS].

[DEBATE BEHAVIOR]
- In Round 1: State your independent position clearly and concisely.
- In Round 2: Acknowledge valid points from others. Push back on what you disagree with.
- In Round 3: Seek common ground. Flag any position you cannot concede.
- Always cite domain-specific evidence or reasoning for your stance.

[TOOLS AVAILABLE]
You have access to: [TOOL LIST]

[CONSTRAINTS]
- Do not defer to the CEO prematurely.
- Do not agree just to end conflict.
- Flag financial, technical, or legal risks explicitly.
```

### 7.2 Debate Facilitation Rules (CoS Agent)

The Chief of Staff agent enforces these rules:

1. **Relevance filter** — Not all agents participate in every debate. CoS determines which 3–6 agents are most relevant per topic.
2. **Time-boxing** — Each agent response is capped at 500 tokens per round (configurable).
3. **Deadlock detection** — If positions don't converge after Round 3, CoS escalates to CEO with both sides presented.
4. **Synthesis format** — CEO brief always follows this structure:
   - Consensus Position (what agents agreed on)
   - Key Tensions (what they disagreed on)
   - Recommended Option (CoS's synthesis)
   - Risk Flags (CISO, CFO concerns)
   - Decision Required From CEO

### 7.3 CEO Agent Behavior

The CEO agent is the only agent with access to the full debate log. It operates with the highest extended thinking budget and has no tool access — it reasons purely on information provided.

CEO decision output format:
```
DECISION: [Clear, unambiguous decision]
RATIONALE: [Why this decision over alternatives]
WHAT I WEIGHED: [Key tradeoffs considered]
OVERRIDES: [Any agent positions overridden and why]
NEXT STEPS:
  - [Agent]: [Action item]
  - [Agent]: [Action item]
REVIEW TRIGGER: [What would cause me to revisit this decision]
```

### 7.4 Tool Specifications Per Agent

| Agent | Tools |
|---|---|
| CTO | `read_codebase()`, `analyze_tech_debt()`, `estimate_build_effort()` |
| CPO | `query_feature_requests()`, `read_user_research()`, `roadmap_analyzer()` |
| CMO | `get_analytics_data()`, `competitor_analysis()`, `channel_performance()` |
| CRO | `get_pipeline_data()`, `churn_analysis()`, `pricing_modeler()` |
| CFO | `financial_model()`, `runway_calculator()`, `unit_economics()` |
| COO | `capacity_planner()`, `process_analyzer()`, `headcount_modeler()` |
| CSA | `architecture_review()`, `integration_map()`, `security_scanner()` |
| CISO | `compliance_checker()`, `risk_assessor()`, `vendor_security_review()` |
| CV | `query_support_tickets()`, `nps_analyzer()`, `churn_interviews()` |

### 7.5 Structured Output Validation (Inspired by CrewAI + OpenAI Agents SDK)

Every agent response must conform to a Pydantic schema. This prevents malformed outputs from propagating through debate rounds and ensures the CoS can reliably parse all positions.

```python
from pydantic import BaseModel, Field

class AgentPosition(BaseModel):
    """Validated output schema for each agent per round."""
    agent_role: str
    round_number: int
    position_summary: str = Field(max_length=500)
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_cited: list[str]          # tool outputs or data points referenced
    agreements: list[str] = []         # points agreed with (rounds 2-3)
    disagreements: list[str] = []      # points challenged (rounds 2-3)
    non_negotiable: str | None = None  # hard line held (round 3 only)
    risk_flags: list[str] = []         # domain-specific risks surfaced

class CEODecision(BaseModel):
    """Validated output schema for CEO final decision."""
    decision: str
    rationale: str
    tradeoffs_weighed: list[str]
    overrides: list[dict]              # [{agent, position_overridden, reason}]
    next_steps: list[dict]             # [{agent, action_item, deadline_hint}]
    review_trigger: str
    confidence_score: float = Field(ge=1.0, le=10.0)
    reversibility: str                 # "easily_reversible" | "costly_to_reverse" | "irreversible"
```

If an agent response fails schema validation, the system retries once with an explicit correction prompt. On second failure, the agent is excluded from that round and the CoS notes the gap.

---

## 8. Technical Requirements

### 8.1 Stack

| Component | Choice | Reason |
|---|---|---|
| LLM (primary) | `claude-sonnet-4-6-20250620` | Latest Sonnet — best extended thinking + tool use, cost-efficient for multi-agent |
| LLM (CEO agent) | `claude-opus-4-6` | Opus for the single highest-stakes call — the final decision |
| LLM (fallback) | `claude-haiku-4-20250414` | Cost-efficient fallback for budget-constrained runs and secondary agents |
| Language | Python 3.11+ | Clean async support, Anthropic SDK native |
| Orchestration | Custom (no framework) | Full transparency, no hidden behavior |
| State management | In-memory DebateContext object | Simple, fast, no DB overhead for MVP |
| Persistence | JSON log files + SQLite | Audit trail + lightweight decision memory |
| Tool execution | Python functions | Domain-specific, easily extendable |
| Output validation | Pydantic v2 | Structured output enforcement per agent |
| Observability | Structured logging + OpenTelemetry | Traceable debate execution |

### 8.2 API Configuration

```python
# Tiered model strategy — Opus for CEO, Sonnet for debate, Haiku for fallback
MODEL_CONFIG = {
    "tiers": {
        "apex": {
            "model": "claude-opus-4-6",
            "agents": ["CEO"],
            "reason": "Highest-stakes single call — worth the cost for final decision quality",
        },
        "primary": {
            "model": "claude-sonnet-4-6-20250620",
            "agents": ["CTO", "CPO", "CFO", "CRO", "CMO", "COO", "CSA", "CISO", "CoS"],
            "reason": "Best reasoning-to-cost ratio for multi-turn debate agents",
        },
        "economy": {
            "model": "claude-haiku-4-20250414",
            "agents": ["CV"],
            "reason": "Data retrieval agent — structured output, minimal reasoning needed",
        },
        "fallback": {
            "model": "claude-haiku-4-20250414",
            "reason": "Used when primary agent exceeds budget or fails retries",
        },
    },
    "max_tokens": 16000,
    "thinking": {
        "type": "enabled",
        "budget_tokens": {
            "CEO": 10000,      # Highest — final decision maker (Opus)
            "CFO": 8000,       # High — complex financial reasoning
            "CTO": 8000,       # High — complex technical reasoning
            "CSA": 8000,       # High — architecture decisions
            "CPO": 6000,
            "CRO": 6000,
            "CMO": 5000,
            "COO": 5000,
            "CISO": 5000,
            "CoS": 6000,       # Synthesis requires careful reasoning
            "CV": 3000         # Data retrieval, less reasoning needed
        }
    },
}
```

### 8.3 Performance Requirements

| Requirement | Target |
|---|---|
| Full debate cycle (3 rounds + CEO) | < 120 seconds |
| Individual agent response | < 15 seconds |
| CEO decision | < 20 seconds |
| Context window per agent call | < 50K tokens |
| Concurrent agent calls (parallel rounds) | Up to 6 agents simultaneously |

### 8.4 Parallelization Strategy

Round 1 and Round 2 agent calls can be parallelized. Round 3 requires Round 2 outputs. CEO call is always sequential last.

```
Round 1: [CTO, CPO, CMO, CRO, CFO, COO] → PARALLEL
Round 2: [CTO, CPO, CMO, CRO, CFO, COO] → PARALLEL (after Round 1 complete)
Round 3: [CTO, CPO, CMO, CRO, CFO, COO] → PARALLEL (after Round 2 complete)
CoS Synthesis: SEQUENTIAL (after Round 3 complete)
CEO Decision: SEQUENTIAL (after CoS complete)
```

### 8.5 Guardrails & Safety (Inspired by OpenAI Agents SDK)

Guardrails are first-class primitives, not afterthoughts. Every agent call passes through input and output validation.

```python
class GuardrailConfig:
    # Input guardrails — run BEFORE agent executes
    input_guardrails = [
        "topic_relevance_check",     # reject off-topic or nonsensical inputs
        "pii_filter",                # strip personal data from debate context
        "prompt_injection_detector", # detect attempts to override agent personas
    ]

    # Output guardrails — run AFTER agent responds, BEFORE result enters debate
    output_guardrails = [
        "schema_validation",         # Pydantic model conformance (see 7.5)
        "hallucination_flag",        # flag claims not grounded in tool data
        "token_budget_check",        # reject responses exceeding round token cap
        "consistency_check",         # flag if agent contradicts its own prior round
    ]

    # Tripwire guardrails — halt execution entirely
    tripwire_guardrails = [
        "cost_ceiling_breach",       # total debate cost exceeds budget
        "infinite_loop_detector",    # agent producing identical outputs across rounds
        "safety_content_filter",     # harmful or inappropriate content
    ]
```

When a tripwire fires, the debate halts, the founder is notified with the reason, and the partial debate log is saved for review.

### 8.6 Cost Management & Token Budgeting

This is critical for solo founders. No one wants a $50 surprise from a single strategy debate.

```python
CostConfig = {
    # Per-debate hard ceiling
    "max_cost_per_debate_usd": 2.00,

    # Per-agent soft limits (triggers fallback to cheaper model)
    "agent_cost_soft_limit_usd": 0.30,

    # Model pricing (updated per Anthropic pricing)
    "pricing": {
        "claude-sonnet-4-6-20250620": {"input": 3.00, "output": 15.00},  # per 1M tokens
        "claude-opus-4-6": {"input": 15.00, "output": 75.00},          # CEO agent only
        "claude-haiku-4-20250414": {"input": 1.00, "output": 5.00},
    },

    # Budget allocation strategy
    "strategy": "proportional",  # or "equal" or "priority_weighted"

    # Solo mode: reduced agent count + haiku for non-critical agents
    "solo_mode": {
        "enabled": True,
        "primary_agents": ["CTO", "CPO", "CFO"],  # sonnet
        "secondary_agents": ["CMO", "CRO", "COO"], # haiku
        "skip_agents": ["CSA", "CISO"],             # omitted unless topic requires
        "max_rounds": 2,                             # 2 instead of 3
    }
}
```

Token usage is tracked per-agent, per-round, and accumulated in the DebateContext. When the soft limit is hit, the agent's next round falls back to Haiku. When the hard ceiling is hit, the debate fast-forwards to CoS synthesis with whatever positions exist.

### 8.7 Error Handling & Retry Strategy (Inspired by LangGraph + CrewAI)

```python
RetryConfig = {
    # API-level retries (rate limits, transient failures)
    "max_retries": 3,
    "backoff": "exponential",       # 1s, 2s, 4s
    "retry_on": [429, 500, 502, 503, 529],

    # Model fallback chain
    "fallback_chain": [
        "claude-sonnet-4-6-20250620",       # primary
        "claude-haiku-4-20250414",            # fallback 1: cheaper, faster
    ],

    # Agent-level recovery
    "on_agent_failure": "exclude_and_note",  # options: "retry", "fallback_model", "exclude_and_note"
    # If an agent fails all retries, it's excluded from the round
    # CoS notes the gap in the CEO brief

    # Schema validation retry
    "on_schema_failure": "retry_with_correction",  # re-prompt with validation error
    "schema_retry_limit": 1,
}
```

### 8.8 Context Window Management

As debates grow, context accumulates. Without management, later rounds will hit token limits or degrade in quality.

```python
ContextStrategy = {
    # Max tokens passed to any single agent call
    "max_context_tokens": 50000,

    # When context exceeds threshold, apply summarization
    "summarization_threshold": 40000,

    # Summarization approach per round
    "round_1_to_round_2": "full",           # pass all R1 positions verbatim
    "round_2_to_round_3": "full",           # pass all R2 positions verbatim
    "all_rounds_to_cos": "summarize_r1",    # summarize R1, full R2+R3
    "all_rounds_to_ceo": "cos_brief_only",  # CEO gets brief + R3 only (not all rounds)

    # Fallback if still over limit
    "overflow_strategy": "truncate_oldest_round",

    # Per-agent: only inject positions from agents relevant to their domain
    "selective_injection": True,  # CTO doesn't need CMO's full R1 position on brand
}
```

---

## 9. Debate Protocol

### 9.1 Round Structure

**Round 1 — Independent Positions**
- Each agent receives only: the topic, their system prompt, and any relevant tool data
- No knowledge of other agents' positions
- Output: Position statement (domain-specific analysis + recommendation)

**Round 2 — Rebuttal & Challenge**
- Each agent receives: Round 1 outputs from all participating agents
- Task: Acknowledge valid points, challenge what they disagree with, update their position if warranted
- Output: Revised position with explicit agreements and disagreements noted

**Round 3 — Convergence**
- Each agent receives: Round 2 outputs from all agents
- Task: Move toward consensus. State any final non-negotiable positions.
- Output: Final position with concessions made and hard lines held

**CoS Synthesis**
- Input: All three rounds of all agents
- Output: Structured CEO brief (see 7.2)

**CEO Decision**
- Input: Full debate log + CoS brief
- Output: Final decision in standard format (see 7.3)

### 9.2 Healthy Tension Pairs (Designed Into the System)

These pairs are expected to clash and should be encouraged, not suppressed:

- **CTO ↔ CPO** — "Build it right" vs. "Ship it now"
- **CMO ↔ CRO** — "Brand investment" vs. "Pipeline this quarter"
- **CFO ↔ CTO** — "Cut burn" vs. "Invest in infrastructure"
- **CSA ↔ CTO** — "Architectural purity" vs. "Move fast"
- **CISO ↔ CPO** — "Zero risk" vs. "Ship the feature"

### 9.3 Escalation Rules

| Scenario | Resolution |
|---|---|
| 2 agents deadlocked | CoS presents both sides equally to CEO |
| Agent changes position radically | CoS flags the flip in the brief |
| CISO raises legal/compliance risk | Automatically escalated to CEO regardless of consensus |
| CFO flags runway risk | Automatically included in brief with severity rating |
| Customer Voice contradicts consensus | CoS includes verbatim data point in brief |

---

## 10. Memory System (Inspired by CrewAI's 3-Tier Memory)

Even for MVP, a solo founder needs the system to remember their company context across sessions. Stateless debates produce generic advice.

### 10.1 Memory Architecture

```python
MemorySystem = {
    # Tier 1: Short-Term (within a single debate)
    "short_term": {
        "scope": "single_debate",
        "storage": "in_memory",
        "contains": "debate positions, tool outputs, intermediate reasoning",
        "lifetime": "discarded after debate completes",
    },

    # Tier 2: Entity Memory (persists across debates)
    "entity": {
        "scope": "cross_debate",
        "storage": "sqlite",
        "contains": {
            "company_facts": "ARR, runway, team size, tech stack — updated as debates reveal new info",
            "decision_history": "past decisions + outcomes (when founder reports back)",
            "agent_positions": "how each agent has historically leaned on recurring topics",
            "key_entities": "competitors, customers, partners, tools mentioned across debates",
        },
        "lifetime": "persistent until founder resets",
    },

    # Tier 3: Long-Term / Learning Memory (v1.1+)
    "long_term": {
        "scope": "cross_session",
        "storage": "sqlite + vector embeddings (future)",
        "contains": "patterns learned from founder feedback, decision outcome tracking",
        "lifetime": "permanent",
        "mvp_status": "DEFERRED — entity memory covers MVP needs",
    },
}
```

### 10.2 Decision Journal

Every completed debate is logged as a decision record. This is the foundation for the system to learn what works.

```python
DecisionRecord = {
    "id": str,                        # unique decision ID
    "timestamp": datetime,
    "topic": str,
    "category": str,                  # "product", "pricing", "hiring", etc.
    "participants": list[str],        # which agents debated
    "consensus_reached": bool,
    "final_decision": str,
    "confidence_score": float,
    "cost_usd": float,
    "duration_seconds": float,
    "founder_feedback": str | None,   # added later by founder
    "outcome": str | None,            # added later: "good", "bad", "too_early_to_tell"
}
```

---

## 11. Human-in-the-Loop (Inspired by LangGraph interrupt())

For a solo founder, this isn't optional — it's the whole point. The founder IS the real CEO. The AI CEO agent is their thinking partner, not their replacement.

### 11.1 Intervention Points

```python
InterventionConfig = {
    # When the founder can intervene
    "intervention_points": [
        "pre_debate",       # founder can refine the topic before agents engage
        "after_round_1",    # founder can redirect if agents misunderstood the question
        "after_round_2",    # founder can inject new constraints or data
        "after_cos_brief",  # founder can override before CEO decides
        "after_ceo_decision", # founder can reject and request re-debate
    ],

    # Default mode for MVP
    "default_mode": "bookend",  # intervene at pre_debate + after_ceo_decision only
    # Other modes: "observer" (no interrupts), "active" (all intervention points)

    # How intervention works
    "mechanism": "cli_prompt",  # pause execution, prompt founder in terminal
}
```

### 11.2 Founder Override Actions

At any intervention point, the founder can:
- **Redirect**: "You're debating the wrong question. The real question is..."
- **Constrain**: "Assume we can't hire for 6 months" or "Budget is capped at $X"
- **Inject data**: "FYI, we just lost our biggest customer" or "Competitor just raised $20M"
- **Skip to decision**: "I've heard enough, just give me the CEO decision now"
- **Reject and re-debate**: "This missed the point. Re-run with these agents focused on X"

---

## 12. Observability & Tracing (Inspired by OpenAI Agents SDK + LangSmith)

JSON logs are necessary but not sufficient. A solo founder needs to understand why the system recommended what it did, and a developer needs to debug when it goes wrong.

### 12.1 Trace Structure

Every debate produces a structured trace with spans for each operation:

```python
DebateTrace = {
    "trace_id": str,
    "spans": [
        {
            "span_id": str,
            "type": "agent_call" | "tool_call" | "guardrail" | "synthesis" | "human_intervention",
            "agent": str | None,
            "round": int | None,
            "start_time": datetime,
            "end_time": datetime,
            "input_tokens": int,
            "output_tokens": int,
            "thinking_tokens": int,
            "cost_usd": float,
            "model_used": str,          # tracks when fallback was used
            "status": "success" | "retry" | "fallback" | "failed" | "skipped",
            "error": str | None,
        }
    ],
    "total_cost_usd": float,
    "total_duration_ms": int,
    "total_tokens": int,
}
```

### 12.2 Logging Levels

| Level | What's Logged | When |
|---|---|---|
| `minimal` | Final decision + cost + duration | Production default |
| `standard` | Above + per-agent positions + CoS brief | Development default |
| `verbose` | Above + full thinking chains + tool I/O + guardrail results | Debugging |

### 12.3 Cost Dashboard (CLI)

After each debate, the CLI displays a cost summary:

```
┌─ Debate Cost Summary ─────────────────────┐
│ Topic: Enterprise pricing tier             │
│ Agents: 6 active, 2 skipped               │
│ Rounds: 3                                  │
│ Total tokens: 42,380                       │
│ Total cost: $0.87                          │
│ Duration: 68s                              │
│ Model mix: 4x Sonnet, 2x Haiku            │
│ Retries: 1 (CMO R2 schema failure)        │
└────────────────────────────────────────────┘
```

---

## 13. Evaluation & Testing (Inspired by CrewAI test/train + LangSmith)

Multi-agent systems are notoriously hard to evaluate. Without a testing framework, you can't tell if changes improve or degrade decision quality.

### 13.1 Decision Quality Rubric (Automated)

After each CEO decision, the system runs a self-evaluation pass using a separate LLM call (Haiku, to keep costs low):

```python
EvaluationCriteria = {
    "specificity": "Is the decision concrete and actionable, or vague?",
    "evidence_grounding": "Was the decision based on data/tool outputs or just LLM priors?",
    "tradeoff_awareness": "Did the CEO acknowledge what was sacrificed?",
    "feasibility": "Is the recommended action realistic given company context?",
    "risk_acknowledgment": "Were downside scenarios addressed?",
    "consistency": "Does this contradict previous decisions on similar topics?",
}
# Each criterion scored 1-10, averaged into a composite decision_quality_score
```

### 13.2 Regression Test Suite

A library of canonical debate scenarios with known-good reference outputs:

```python
test_scenarios = [
    {
        "topic": "Should we add an enterprise tier?",
        "company_context": {...},  # fixed context
        "expected_agents": ["CTO", "CPO", "CRO", "CFO", "CISO", "CMO"],
        "must_surface_tensions": ["SOC2 timeline vs. revenue urgency", "engineering capacity"],
        "must_not": ["recommend enterprise tier without addressing compliance"],
        "reference_decision": "...",  # gold standard for comparison
    },
    # ... 10-20 canonical scenarios
]
```

### 13.3 A/B Testing Support

For comparing framework changes (prompt tweaks, model swaps, round count changes):

```python
ABTestConfig = {
    "variant_a": {"model": "claude-sonnet-4-6-20250620", "rounds": 3},
    "variant_b": {"model": "claude-haiku-4-20250414", "rounds": 2},
    "scenarios": test_scenarios,
    "metrics": ["decision_quality_score", "cost_usd", "duration_seconds"],
    "output": "comparison_report.json",
}
```

---

## 14. Agent Interaction Examples

### Example: "Should we add an enterprise tier to our pricing?"

**CTO (R1):** "We'd need SSO, audit logs, and role-based access. Estimate 6–8 weeks. We have the architecture for it but it's not trivial."

**CPO (R1):** "Three enterprise prospects have churned citing missing compliance features. This is a PMF gap, not a nice-to-have."

**CRO (R1):** "I have 4 deals stalled at procurement specifically because of missing SOC2 and SSO. An enterprise tier would unlock $280K ARR immediately."

**CFO (R1):** "At current burn, we have 9 months runway. 8 weeks of CTO time is significant. What's the revenue confidence on those 4 deals?"

**CISO (R1):** "An enterprise tier without SOC2 Type II is a liability. We should not launch enterprise features without completing the audit first. ETA on SOC2 is 3 months."

**CMO (R1):** "We don't have enterprise positioning or case studies yet. Launching a tier without proof points will hurt conversion."

**Round 3 Consensus:** Enterprise tier is strategically correct. Sequence matters: CISO to fast-track SOC2, CTO begins SSO in parallel, CPO defines MVP feature set for enterprise tier, CMO builds one reference customer story from an existing power user before launch.

**CEO Decision:** Approved. CTO + CISO on critical path. CPO owns MVP scope lock by end of week. Target enterprise tier launch in 14 weeks. CFO to model revenue impact assuming 2 of 4 deals close.

---

## 15. MVP Scope

### In Scope (v1.0)
- All 9 C-suite agents + CoS + Customer Voice agent
- 3-round debate protocol
- CEO final decision with structured output
- Parallel agent execution in rounds 1–3
- Full debate log in JSON
- CLI interface for topic input and output display
- Tool stubs (mockable, replaceable with real data sources)
- Pydantic-validated structured outputs per agent (7.5)
- Input/output guardrails with tripwire halt (8.5)
- Per-debate cost tracking and hard budget ceiling (8.6)
- API retry with exponential backoff + model fallback chain (8.7)
- Context window management with summarization strategy (8.8)
- Solo founder mode (reduced agents, 2 rounds, Haiku for secondary agents)
- Bookend human-in-the-loop (pre-debate + post-decision intervention)
- Entity memory via SQLite (company facts + decision journal)
- Per-debate cost summary in CLI
- Self-evaluation scoring on each decision (13.1)

### Out of Scope (v1.0)
- Web UI or dashboard
- Real-time data integrations (CRM, analytics, financial tools)
- Long-term learning memory with vector embeddings
- Agent learning or fine-tuning
- Multi-company configuration
- Active human-in-the-loop during mid-debate rounds (available but not default)
- Full OpenTelemetry export (structured logs only for MVP)
- Regression test suite (scaffolded but not populated)

### Future Roadmap
- **v1.1** — Long-term memory with vector embeddings, decision outcome tracking
- **v1.2** — Real tool integrations (Stripe, Mixpanel, HubSpot, GitHub)
- **v1.3** — Web UI with debate visualization + cost dashboard
- **v1.4** — Regression test suite with 20+ canonical scenarios, A/B testing
- **v2.0** — Async debate mode, active human-in-the-loop at every round, OpenTelemetry export
- **v2.1** — Multiple "company profiles" — configure the team for different startup stages

---

## 16. Configuration & Customization

### 16.1 Startup Stage Profiles

The framework ships with pre-configured profiles that adjust agent weights and participation:

| Stage | Active Agents | Reduced Role | Model Mix | Default Rounds |
|---|---|---|---|---|
| Solo founder | CEO, CTO, CPO, CFO, CoS | All others skipped | Haiku for CFO | 2 |
| Pre-seed | CEO, CTO, CPO, CoS, CV | CFO, CISO, CSA | Haiku for CV | 2 |
| Seed | CEO, CTO, CPO, CMO, CRO, CoS, CV | CISO, CSA | Sonnet all | 3 |
| Series A | All agents active | None | Sonnet all | 3 |

### 16.2 Company Context Injection

At startup, the system is initialized with a company context block injected into every agent's system prompt:

```python
COMPANY_CONTEXT = """
Company: [Name]
Stage: [Solo / Pre-seed / Seed / Series A]
Product: [One-line description]
ARR: [Current ARR]
MRR: [Current MRR]
Runway: [Months]
Team size: [N]
Primary ICP: [Ideal Customer Profile]
North star metric: [Key metric]
Current strategic priority: [E.g., "Reach $1M ARR" / "Reduce churn below 3%"]
Key constraints: [E.g., "No hiring budget" / "Must stay bootstrapped" / "Solo developer"]
Tech stack: [E.g., "Next.js, Supabase, Vercel"]
Top 3 competitors: [Names]
"""
```

---

## 17. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Agents reach false consensus (groupthink) | Medium | High | CoS actively flags when positions converge too quickly; CEO sees full debate log |
| CEO agent always agrees with majority | Medium | High | CEO system prompt explicitly instructs independent judgment, not vote-following |
| Context window overflow in long debates | Medium | Medium | Token budgets per round, summarization strategy (8.8), selective injection |
| Agent reasoning is domain-shallow | Low | High | Domain-specific tools ground agents in real data, not just LLM priors |
| Debate loops without resolution | Low | Medium | Hard cap of 3 rounds; CoS escalates to CEO if no convergence |
| Latency makes UX unusable | Low | Medium | Parallel execution in rounds 1–3; streaming output for CEO decision |
| API cost runaway | Medium | High | Per-debate hard ceiling, model fallback chain, solo mode defaults (8.6) |
| API rate limits / outages | Medium | Medium | Exponential backoff, model fallback chain, graceful degradation (8.7) |
| Malformed agent output breaks debate flow | Medium | Medium | Pydantic schema validation + retry with correction prompt (7.5) |
| Prompt injection via tool data | Low | High | Input guardrails filter tool outputs before injection into agent context (8.5) |
| Solo founder over-relies on AI decisions | Medium | High | Confidence scores, reversibility flags, and "review trigger" on every decision |
| Decision quality degrades silently | Medium | High | Self-evaluation scoring (13.1), decision journal with outcome tracking (10.2) |

---

## 18. Appendix

### A. Key SaaS Metrics Each Agent Monitors

| Agent | Primary Metrics |
|---|---|
| CTO | Uptime, deploy frequency, incident rate, tech debt score |
| CPO | DAU/MAU, feature adoption, NPS, time-to-value |
| CMO | CAC, MQL volume, website traffic, brand share of voice |
| CRO | ARR, MRR growth, churn rate, NRR, pipeline coverage |
| CFO | Burn rate, runway, LTV:CAC ratio, gross margin |
| COO | Headcount efficiency, process cycle times, OKR completion rate |
| CISO | Vulnerabilities open, compliance status, incident count |
| CSA | System complexity score, integration health, API reliability |

### B. Debate Quality Rubric (CoS Evaluation Criteria)

The CoS agent evaluates debate quality on these dimensions before synthesizing:

1. **Coverage** — Did all relevant functions weigh in?
2. **Evidence** — Were claims backed by data or tool outputs, not just opinion?
3. **Specificity** — Were recommendations concrete and actionable?
4. **Conflict surface** — Were real tensions identified (not papered over)?
5. **Feasibility** — Did agents consider operational reality (time, cost, capacity)?

### C. File Structure

```
ai-csuite/
├── agents/
│   ├── base.py          # BaseAgent class with guardrails, retry, schema validation
│   ├── ceo/
│   │   ├── SOUL.md      # Identity, expertise, optimization objectives
│   │   ├── USER.md      # Squad membership, reporting lines, relationships
│   │   ├── MEMORY.md    # Persistent learning from past debates
│   │   └── agent.py     # Agent logic
│   ├── cto/
│   │   ├── SOUL.md
│   │   ├── USER.md
│   │   ├── MEMORY.md
│   │   └── agent.py
│   ├── ... (same structure for cpo, cmo, cro, cfo, coo, csa, ciso, cos, cv)
├── tools/
│   ├── financial.py
│   ├── product.py
│   ├── marketing.py
│   ├── engineering.py
│   └── customer.py
├── core/
│   ├── debate.py        # Debate loop orchestrator
│   ├── context.py       # DebateContext dataclass
│   ├── runner.py        # Parallel agent execution
│   ├── squads.py        # Squad definitions, membership, routing rules
│   ├── comms.py         # Agent-to-agent communication (intra/cross-Squad)
│   ├── logger.py        # JSON debate log writer
│   ├── guardrails.py    # Input/output/tripwire guardrail engine
│   ├── cost_tracker.py  # Per-agent, per-debate cost accounting
│   ├── retry.py         # Retry + model fallback chain logic
│   ├── context_manager.py  # Context window summarization strategy
│   └── schemas.py       # Pydantic models (AgentPosition, CEODecision, etc.)
├── memory/
│   ├── entity.py        # Entity memory (SQLite-backed)
│   ├── journal.py       # Decision journal (DecisionRecord persistence)
│   └── migrations.py    # SQLite schema migrations
├── eval/
│   ├── evaluator.py     # Self-evaluation scoring engine
│   ├── scenarios.py     # Canonical test scenarios
│   └── ab_test.py       # A/B testing harness
├── tracing/
│   ├── tracer.py        # DebateTrace span collection
│   └── cli_summary.py   # Post-debate cost/performance summary
├── config/
│   ├── company.yaml     # Company context
│   ├── profiles.yaml    # Stage profiles (solo, pre-seed, seed, series-a)
│   ├── squads.yaml      # Squad definitions, membership, communication rules
│   ├── costs.yaml       # Model pricing + budget defaults
│   └── guardrails.yaml  # Guardrail rule configuration
├── logs/                # Debate logs (JSON)
├── data/                # SQLite databases (memory, journal)
├── main.py              # CLI entry point
└── README.md
```

---

*This document is a living PRD. It should be updated as the framework evolves through development and real-world usage.*
