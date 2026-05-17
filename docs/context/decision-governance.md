# Decision & Governance

## decision chain
The full decision chain is: CRO proposes → CFO evaluates financials → CoS converges options → CEO approves direction → COO defines execution plan → AutonomyGate asks user → user approves → subagents execute.

**Meaning:** The agent team prepares a ready-to-approve plan. The user is always the final decision maker before execution begins.

**Implication:** No execution should start without user approval through AutonomyGate. The system's job is to reduce the user's decision burden, not to bypass the user.

## autonomy gate
AutonomyGate must be wired into the engine's directive and execution flow as the user authorization layer.

**Meaning:** The user is the supreme authority. AutonomyGate determines what the system may do autonomously vs. what requires human approval.

**Implication:** Connect the existing AutonomyGate class into KompanyEngine.process_directive() and ProjectRunner.run() instead of leaving it unused.

## autonomy tiers
AutonomyGate should support tiered authorization levels that the user can configure.

**Meaning:** Not everything needs user approval. Auto-execute for low-risk internal actions (cost logging, analysis, queries). Notify-after for low-risk completions and small expenses. Approve-before for overspend execution, external procurement, and directional decisions.

**Implication:** Default to the most conservative tier. Let the user progressively loosen control as trust builds.

## decision journal
Journal must be wired into the engine to record every key CEO decision with rationale and context.

**Meaning:** The existing Journal class and decisions table should actually be called when CEO classifies, approves, or rejects directives.

**Implication:** Decision logs become the primary data source for retrospectives and self-learning.

## constitution
The system must have a constitution defining immutable rules that no agent or process may override.

**Meaning:** Inviolable rules include: user is always the supreme decision maker; all spending must be recorded in the ledger; no self-modification of Python source code; the constitution itself cannot be auto-modified; decision journal entries are append-only and never deleted; team must give honest assessments and never provide falsely optimistic evaluations; user-declared exclusion domains must be respected and no plan may involve excluded domains.

**Implication:** Everything else can evolve through self-learning, but these rules are the hard floor. Create a CONSTITUTION.md file to codify them.

## safety guardrails
Hard safety limits operate independently of agent judgment to prevent runaway behavior.

**Meaning:** (1) Per-transaction spending cap — user-configured hard ceiling; anything above is approve-before regardless of autonomy tier. (2) Daily spending cap — total daily spend has a ceiling; once reached, all spending operations pause until user confirms. (3) Content publishing review — all externally published content (social media, email, website) passes through CISO automated review for sensitive information and brand risk; high-risk items require user confirmation. (4) Rollback log — every external operation records enough detail for manual reversal (what was sent, where, when).

**Implication:** These are circuit breakers, not agent decisions. They fire even if every agent in the system agrees an action is safe. CISO enforces them but cannot override them.

## tool authorization
All external tool calls should be authorized by the core engine, not by subagents directly.

**Meaning:** Subagents may propose actions, but the central orchestrator approves and performs tool use.

**Implication:** Use CISO as a safety backstop and keep permissions centralized.

## tool registry
Tools are registered as declarative configuration with CISO maintaining a permission whitelist per autonomy tier.

**Meaning:** Each tool declares a name, category (information-gathering, content-publishing, code-operations, file-operations), and risk level. CISO sets default autonomy tiers by risk: low-risk (web search) auto-execute, medium-risk (email) notify-after, high-risk (deploy, publish) approve-before. Users can override any tool's tier.

**Implication:** Adding a new tool means adding a config entry and having CISO assign its risk level. No code changes needed for permission logic.

## quality assurance
Every subagent's output is reviewed by the responsible C-level agent before delivery or publication.

**Meaning:** Builder's code → CTO review. Writer's content → CMO (external) or CPO (product docs) review. Researcher's findings → delivered to requester with source and timestamp annotations. Analyst's analysis → CoS or requester review. Procurement's external actions → CISO security review + AutonomyGate. No separate QA agent; existing role responsibilities cover quality.

**Implication:** No subagent output reaches the outside world without at least one C-level review pass.

## compliance and security review
All outputs and actions pass through compliance and security checks before execution.

**Meaning:** CISO runs automated checks on every externally-facing action: data privacy compliance, sensitive information leakage, brand safety, legal risk. Compliance rules are maintained as a configurable checklist that CISO evaluates against. Security checks cover: API key exposure, credential handling, injection risks in generated content, and permission scope validation. Failures block execution and escalate to the user.

**Implication:** Compliance and security are not optional review steps — they are mandatory gates in the execution pipeline, enforced by CISO independently of other agents' judgments.

## feasibility assessment
Before execution begins, the team must deliver an honest feasibility assessment with tiered options.

**Meaning:** CTO evaluates technical feasibility, CRO evaluates revenue feasibility, CFO evaluates financial feasibility, COO evaluates execution timeline. CoS synthesizes into three tiers: ✅ feasible (reasonable path within constraints), ⚠️ conditionally feasible (needs more time, budget, or reduced scope — with specific suggestions), ❌ not feasible (no reasonable path — with explanation). The user decides: accept, adjust parameters, or abandon. The team must never give falsely optimistic assessments to please the user.

**Implication:** This is the first real test of team judgment on every new directive. Honest assessment is a constitutional-level obligation — the system's credibility depends on it.
