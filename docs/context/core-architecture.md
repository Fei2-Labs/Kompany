# Core Architecture

## agent system
Kompany is an agent system like Claude Code, OpenClaw, Hermes, and Codex.

**Meaning:** The product identity is the shared orchestration core and its agents, not any single harness or interface.

**Implication:** CLI, API, MCP, and SDK are adapters around the same system.

## orchestration model
Kompany should use a unified orchestration engine with role-based agents rather than a loose swarm.

**Meaning:** A central coordinator assigns work to specialized agents with explicit responsibilities.

**Implication:** Keep decision-making, routing, and state transitions in the core engine instead of spreading them across interfaces.

## agent communication
Agents communicate through direct function calls via the engine, not message queues.

**Meaning:** All agent-to-agent interaction is mediated by KompanyEngine in-process. No async message bus needed at this stage.

**Implication:** This keeps communication auditable and debuggable. Introduce message-based communication only if the system later needs distributed deployment.

## debate termination
Debates use fixed round limits as a ceiling with early convergence exit.

**Meaning:** CoS evaluates consensus after each round. If all agents agree (no major dissent), debate ends early to save tokens. If the round limit is reached with unresolved disagreements, CoS synthesizes a decision packet for CEO to make the final call. Round limits remain controlled by stage profile as cost protection.

**Implication:** Debates are never open-ended. CoS owns convergence judgment; CEO owns tie-breaking.

## context management
Agent LLM calls use precision context injection, not full-state dumps.

**Meaning:** Each LLM call receives only context relevant to the current task. Channel conversations are isolated by company, project, channel account, chat, thread, sender, active agent, and session epoch; interfaces pass this identity through a shared engine-level context contract. Specialist handoffs persist a typed owner transition and restore the previous owner if recipient startup fails. Direct specialist replies receive no tools and must not claim external actions. Multi-agent requests remain CEO-owned and create a durable delegation linked to bounded project tasks and parent/child run IDs. Delegated children receive a fresh structured packet rather than conversation history or unscoped agent memory; only the CEO synthesizes child results into a channel-visible answer. COO assembles a "working memory" packet per ordinary project task from long-term memory (agent_memories). Documents exceeding ~200 lines must be split into index + sub-files; agents read sub-files on demand. After each task, COO extracts key learnings from working memory into long-term memory. CoS compresses verbose records into distilled knowledge during the distillation cycle.

**Implication:** Context discipline is an operational requirement, not a nice-to-have. Without it, agents degrade as the system accumulates data.

## language strategy
Agent internal communication uses maximally token-efficient language; user-facing content follows user preference.

**Meaning:** (1) Internal agent communication (debates, memory, audit log) uses the most compressed language possible — caveman-style shorthand, classical Chinese, or any form that minimizes tokens while preserving meaning. Accuracy must not be sacrificed for brevity. (2) User interaction matches the user's current language (Chinese in → Chinese out). (3) User-visible content (decision journal, reports, notifications) defaults to English but is configurable via user language preference. (4) Externally published content (social media, email, courses) follows target market language, independent of internal communication.

**Implication:** Token savings on internal communication compound across every debate round, every memory write, every heartbeat cycle. CTO + CSA should design and validate the internal compression format to ensure no miscommunication between agents.
