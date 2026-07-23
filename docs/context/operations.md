# Operations

## budget policy
The system may exceed its budget, but the agent team must collectively judge whether the overspend is reasonable.

**Meaning:** There is no hard overspend cap. Instead, CRO evaluates ROI, CFO independently assesses financial viability, and CEO makes the final call. If a plan is unrealistic (e.g. 10x overspend with a 5-year payback), the agents should reject it themselves.

**Implication:** Budget discipline is an emergent capability of the agent team, not a hardcoded rule. When the team misjudges, the retrospective and self-learning loop should capture the mistake and improve future judgment.

## task execution sessions
Project tasks execute as real multi-turn agentic sessions in per-project workspaces, capped per task and gated by the founder approval inbox.

**Meaning:** When the founder configures a model source, every ProjectRunner task runs via an external agent CLI (Claude Code CLI, Codex CLI, or opencode — derived from the source, never chosen directly) inside the project's git workspace. The CEO assigns each task a budget cap at decomposition ($0.50 default, $5 ceiling); the project's budget envelope is the hard outer cap. A session that hits its cap pauses and files a budget-increase approval; an exhausted envelope parks tasks and files one top-up approval per project — approving these cards actually raises the cap / funds the envelope. Side-effecting tool calls inside a session pause for inbox approval (~120s window; an unanswered request stays in the inbox and redeems on the task's next run).

**Implication:** "Completed" requires evidence — a workspace diff or real tool results, never keyword guessing. Denied tool use classifies the task as blocked with the pending approval as the founder's next step. Sessions persist their id on the task, so a re-run continues from where it stopped instead of restarting.

## daemon tick loop
The engine ticks autonomously on a fixed interval, advancing real work in strictly bounded slices when no founder session is open.

**Meaning:** Every `tick_interval_seconds` (default 300) the ticker runs, in order: the runtime gate (suspended → record an idle tick, nothing else), the existing heartbeat (pending approvals, active projects, monthly subscription fee booking — deferred while suspended, booked on the first tick after resume), advance work (at most one pending task of one active project, skipping projects with a pending envelope top-up or budget-increase approval; failed tasks are never auto-retried — retry stays a founder-initiated resume), and housekeeping (keep the last 500 tick records, trim old episodes). Each tick is recorded in `daemon_ticks` and published as a `daemon.tick` SSE event.

**Implication:** Unattended spend is founder-bounded by construction: one task slice per tick × per-task budget caps × envelope hard caps, with the approval inbox still gating everything it gated before. The founder brake is `kompany suspend` — no separate kill mechanism. Tick visibility joins the existing status/observability surfaces (`ticker` block, recent ticks) rather than a new operation.

## financial monitoring
CFO should continuously monitor the financial health of every running project and trigger alerts when actuals diverge from projections.

**Meaning:** CFO acts as a sentinel. When a project starts losing money or drifting from its break-even path, CFO raises an alert. CRO assesses whether the cause is market shift or execution failure. CEO recommends a response (scale down, pivot, cut losses, or persist). AutonomyGate asks the user for the final call.

**Implication:** Anomaly detection is a judgment call by CFO, not a hardcoded threshold. This judgment improves over time through the retrospective and self-learning loop. If the user does not respond in time, the system defaults to the most conservative action (pause new spending).

## multi-project concurrency
No hard limit on concurrent projects. The agent team decides based on resource and financial capacity.

**Meaning:** COO assesses resource load, CFO assesses financial runway, CEO makes the recommendation, user approves.

**Implication:** Like budget discipline, project concurrency is an emergent team capability, not a system constant.

## multi-business operation
The system naturally evolves into managing multiple business lines as projects succeed and spawn derivatives.

**Meaning:** Execution subagents are a shared capability pool, not bound to specific business lines. COO schedules and prioritizes across all lines. C-level agents (CMO, CRO, etc.) manage strategy across all business lines simultaneously but tailor plans per line.

**Implication:** If a business line grows large enough to need dedicated agents, COO creates temporary agents through the standard lifecycle process. Default is shared resources with COO coordinating priorities.

## project relationships
Projects can reference each other as related projects, but without rigid dependency management.

**Meaning:** Agents can read memories and outputs from related projects. COO can identify cross-project synergies (e.g. e-commerce content reused for a course). No hard dependency chains, cross-project approval flows, or resource locking.

**Implication:** Cross-project collaboration is an emergent team capability. COO spots synergies, CoS coordinates conflicts, CEO sets priorities. Keep it lightweight like a real small company.

## post-completion flow
After a project completes, the agent team should proactively propose next steps rather than wait idle.

**Meaning:** COO confirms completion, CFO settles accounts, CoS runs a retrospective, then CRO + CPO propose options: scale up the current project, pursue a derivative niche, pivot direction, or pause. CoS converges options, CEO ranks them, AutonomyGate presents to user for approval.

**Implication:** The team acts like a proactive executive team that always has a recommendation ready, but never executes without user sign-off.

## procurement scope
Procurement should initially be limited to information gathering and content publishing, not real payments or transactions.

**Meaning:** Allowed: web search, API calls, email, social media posts, GitHub repos, website deployment. Not yet: Stripe payments, bank transfers, real purchase orders.

**Implication:** Expand to payment-capable tools only after the system has built enough self-learning history and user trust.

## external service integration
External services use an adapter pattern, registered as tools in the tool registry.

**Meaning:** Each external service (social media API, e-commerce platform, email service, website deployment) is wrapped in an adapter with a unified `execute(action, params) → result` interface. Adapters are registered as tools in the tool registry, inheriting CISO permission management and autonomy tiers. Agents receive only opaque secret references. A customer-operated, provider-neutral credential broker verifies company, project, agent, connector, action, destination, expiry, approval, and use-count scope before issuing worker-bound leases. Core rejects plaintext broker responses; raw credentials never enter prompts, audit events, tool results, or channel messages. Adding a new service means writing an adapter, registering it, and having CISO assign permissions — no core code changes.

**Implication:** The adapter pattern keeps the core engine decoupled from any specific external service or secret-store provider. Unlock, MFA, re-authentication, and replacement requirements pause the original task as a durable structured blocker; a successful broker preflight resumes that same task checkpoint. Founder approval is single-use: issuance is sent with an idempotency key, and an ambiguous timeout remains fail-closed instead of making the approval reusable. Broker availability and declared capabilities are exposed at `GET /credential-broker/status`.

## initialization flow
User provides five inputs at startup: company name, starting capital, the goal, time horizon, and exclusions. The agent team designs the product/service plan and presents it to the user for approval.

**Meaning:** The user states what they want to achieve, how much they have, how long they're willing to wait, and what domains or methods are off-limits. The team proposes a path (product, service, revenue model) and evaluates feasibility against the time horizon — the goal might be achievable as stated, require more time, or be unrealistic entirely. The team must be honest in this assessment. The user approves or adjusts before execution starts.

**Implication:** System defaults to solo stage. Do not ask the user to fill in product details or configuration at init time. Time horizon and exclusions are constraints that shape every downstream decision — CRO's revenue plans, COO's execution schedules, and CTO's technical approach must all respect them.

## stage upgrade
Stage upgrades (solo → pre-seed → seed → series-a) should be proposed by the agent team based on judgment, not hardcoded thresholds.

**Meaning:** CEO + CFO assess whether project complexity, financial scale, or concurrency has outgrown the current stage. Team proposes upgrade, user approves.

**Implication:** Stage controls which agents participate in debates and how many rounds run. Do not auto-upgrade without user approval.
