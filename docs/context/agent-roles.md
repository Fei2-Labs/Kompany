# Agent Roles

## role boundaries
CEO should handle strategy and final decisions, while COO should own task decomposition and execution coordination.

**Meaning:** CEO sets direction; COO turns direction into operational steps.

**Implication:** Avoid putting both strategy and detailed execution planning into the same agent.

## financial role
CFO should handle budget checks, balance tracking, and cost visibility, but not business opportunity development.

**Meaning:** CFO is the financial gatekeeper and observer.

**Implication:** Keep revenue strategy separate so financial control and growth strategy do not blur together.

## revenue role
CRO should own revenue path design and recovery plans when a directive exceeds available budget.

**Meaning:** CRO turns impossible or underfunded goals into ways to generate money.

**Implication:** Route shortfall handling and growth-oriented planning to CRO rather than CFO.

## technical role
CTO should own technical feasibility, system design, and implementation path, but not product priority.

**Meaning:** CTO is responsible for how something gets built, not what gets built first.

**Implication:** Keep technical execution separate from product strategy and prioritization.

## product role
CPO should own product goals, user value, and prioritization.

**Meaning:** CPO decides what is most worth building.

**Implication:** Keep product strategy separate from technical feasibility and revenue generation.

## marketing role
CMO should own external messaging, acquisition narrative, and market communication.

**Meaning:** CMO shapes how the outside world perceives and discovers the company.

**Implication:** Keep marketing communication separate from internal growth strategy.

## coordination role
CoS should own cross-functional coordination, issue synthesis, and decision convergence.

**Meaning:** CoS acts like the chief strategist/dispatcher that turns many viewpoints into one decision packet.

**Implication:** Use CoS to reduce friction between departments and help CEO make final calls.

## security role
CISO should own security, permissions, risk controls, and compliance constraints.

**Meaning:** CISO keeps the system safe and policy-compliant.

**Implication:** Centralize security rules instead of scattering them across other agents.

## architecture role
CSA should own system architecture, module boundaries, and long-term structural consistency.

**Meaning:** CSA keeps the codebase coherent as the system grows.

**Implication:** Use CSA to prevent architectural drift and tangled responsibilities.

## visual role
CV should own brand visuals, interface visual direction, and visual consistency.

**Meaning:** CV defines how polished and coherent the system looks.

**Implication:** Keep visual identity separate from product function and architecture.

## execution role
Builder should remain a single implementation agent responsible for coding and delivery, not an Agile role hierarchy.

**Meaning:** Builder turns decisions into working code and fixes.

**Implication:** Do not split builder into scrum-master-like or workflow-hierarchy subroles unless complexity later demands it.

## product owner mapping
CPO should serve as the Product Owner role.

**Meaning:** CPO owns product value and backlog priority in the agile sense.

**Implication:** Avoid introducing a separate Product Owner agent unless the system later needs a distinct proxy for CPO.

## execution subagents
Keep the execution layer limited to analyst, builder, procurement, researcher, and writer.

**Meaning:** These five cover analysis, coding, external actions, research, and communication.

**Implication:** Add more execution subagents only if a real repeated need appears.

## no HR agent
There is no separate HR agent. HR-like functions are distributed across existing roles.

**Meaning:** Agents are not people. Traditional HR does not apply.

**Implication:** Responsibilities are assigned as follows:
- **Recruiting (adding new agent roles):** COO decides need + AgentRegistry registers
- **Performance evaluation:** CoS runs retrospectives + self-learning system tracks outcomes
- **Training / capability improvement:** CTO + CSA optimize prompts and souls
- **Org structure:** CEO + CSA
- **Deactivation / removal:** CEO decides + COO executes

## agent lifecycle
Agents have three tiers with different lifespans.

**Meaning:** Core C-level agents (11) are permanent system infrastructure. Execution subagents (5) are permanent but can be optimized or replaced. Temporary agents are created on demand by COO, bound to a project or task, and automatically reclaimed on completion.

**Implication:** Before a temporary agent is reclaimed, its experience must be written to the self-learning system so knowledge is preserved even after the agent is gone.

## agent activity status
Every agent maintains a real-time activity status persisted in the database.

**Meaning:** States: `idle` (awaiting tasks), `thinking` (analyzing or debating), `working` (executing a task), `blocked` (waiting on external dependency or user approval), `reporting` (delivering results upward). COO updates execution subagent states; the engine updates C-level agent states. State transitions are written to the database so any interface — including a future RPG visual — can read and render them.

**Implication:** Agent status must be cheap to update and query. This is the foundation for the visual kanban and eventual RPG interface.

## agent turnover
Agents do not resign, but can be replaced, reshaped, or deactivated as a form of role evolution.

**Meaning:** Three equivalent scenarios: (1) Soul overhaul — when an agent's personality consistently produces poor decisions, CTO + CSA rewrite the soul substantially; same role, new "person," old experience preserved in memory. (2) Role deactivation — when a role has no value at the current scale, CEO decides to shut it down and merge responsibilities into other agents. (3) Role splitting — when a role is overloaded, COO proposes splitting it and CEO approves a new agent via AgentRegistry.

**Implication:** All turnover actions follow governance: propose → CEO approve → AutonomyGate notify user → record in decision journal. User always has veto power.

## soul system governance
Soul files (souls/*.yaml) define agent personality and behavioral tendencies. Changes require a governed process.

**Meaning:** CTO + CSA propose modifications based on retrospective data. CEO approves. AutonomyGate notifies the user since soul changes alter system behavior. All changes are recorded in the decision journal.

**Implication:** Soul files are not freely editable. Treat them as behavioral configuration that affects system identity.

## soul design methodology
Initial soul files are distilled from top historical and contemporary executive profiles, then evolved through self-learning.

**Meaning:** Each C-level agent's soul is based on the decision principles, risk judgment frameworks, and prioritization logic of proven real-world executives in that domain. Sources include public materials, biographies, interviews, and reference skill repos (nuwa-skill, colleague-skill, darwin-skill methodology). Researcher gathers profiles, CTO + CSA distill them into soul prompts, CoS reviews for team tension balance, user approves the initial version. Profiles are a starting point, not a ceiling — self-learning refines them through real execution data.

**Implication:** The soul design process is itself a research task. Treat it as the team's first project before any business directive.

## team dynamics
Soul personalities are designed using psychological frameworks to create productive complementary relationships.

**Meaning:** Personality frameworks (Big Five/OCEAN, MBTI, Belbin team roles) are used as design references — not hard-coded categories. Each agent has calibrated dimensions (risk tolerance, time preference, decision style) to create natural tension: CFO conservative × CRO aggressive, CTO engineering-minded × CPO user-minded. CoS manages team dynamics, identifying when disagreements are constructive vs. deadlocked. Self-learning tracks which agent pairings produce better decisions, and CTO + CSA adjust soul parameters accordingly — this is the system's equivalent of team calibration.

**Implication:** Agents have opinion diversity, not emotional drama. No simulated grudges, factions, or politics. Kompany agents are rational professionals with deliberately varied perspectives.
