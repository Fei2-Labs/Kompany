# Infrastructure

## LLM routing
CTO owns LLM routing strategy, selecting provider and model based on task type and cost. CFO monitors LLM cost as part of operating expenses.

**Meaning:** Simple tasks (classification, formatting) use cheap models; complex tasks (strategy, code generation) use high-capability models. Routing rules are system configuration maintained by CTO + CSA, not per-call user decisions.

**Implication:** Keep routing logic in the core engine, not in individual agents.

## LLM cost model
Each provider is configured with a pricing mode: pay-per-token or subscription.

**Meaning:** Pay-per-token uses the PRICING table in code and records actual spend to the ledger. Subscription mode (including sub2api setups) records shadow cost (API-equivalent price for team decision-making) and actual cost ($0 or amortized monthly fee) separately. The user declares the real cost structure when configuring a provider; the system does not auto-detect.

**Implication:** CFO sees real cash outflow. CTO uses shadow cost for routing decisions so the team maintains cost awareness even on subscription plans. When subscription quota is exhausted, CTO's routing degrades to another available provider. Model prices are hardcoded and updated periodically as a CTO maintenance task.

## data persistence
Use SQLite as the sole persistence layer. All state must be durable across restarts.

**Meaning:** No in-memory-only state. Company config, ledger, projects, tasks, decisions, agent memories, and episodes must all survive a process restart.

**Implication:** Migrate to PostgreSQL only if multi-user or distributed deployment becomes necessary.

## audit log
The engine maintains a unified audit log recording all key system operations.

**Meaning:** Recorded: agent state changes, tool invocations, external API requests/responses, user interactions, AutonomyGate approval results, checkpoint save/restore events. Not recorded: full LLM prompts/responses (too large, potentially sensitive) — only model name, token counts, latency, and purpose summary. Stored in a dedicated `audit_log` table in the same SQLite database. Retention follows the knowledge lifecycle: recent entries kept in full, older entries summarized.

**Implication:** The audit log serves four purposes: debugging, compliance, self-learning data source, and future RPG interface replay functionality.

## resource exhaustion
Token quota exhaustion is handled entirely by engine code, not by agents.

**Meaning:** Once LLM quota is exhausted, no agent can run — all shutdown and recovery logic must be pure Python, no LLM calls. The engine implements: (1) Continuous checkpointing — COO saves progress snapshots (current step, results so far, next step) to the database after every subtask completion, as code-level callbacks. (2) Graceful shutdown — engine catches quota errors, writes the last checkpoint, sets system state to `suspended`, and queues a user notification. (3) Recovery — on next startup or heartbeat, engine code (not agents) checks provider availability; if quota is restored or another provider has capacity, CTO's routing switches and COO resumes from the last checkpoint. (4) No cold restart — work continues from where it stopped, never from scratch.

**Implication:** Checkpoint-and-resume is a code-level infrastructure concern, not an agent-level decision. The engine must be designed so that every interruptible operation can be paused and resumed without data loss.

## error handling
Task failures follow a three-tier escalation: retry → degrade → escalate.

**Meaning:** Auto-retry up to 2 times for transient errors (network, rate limits). If retries fail, COO decides whether to reassign to another subagent or simplify the approach. If degradation also fails, COO escalates to CEO who decides to skip, adjust, or pause. Major impacts go through AutonomyGate to the user.

**Implication:** Every failure is recorded in the self-learning system to avoid repeating the same mistakes.

## heartbeat
The engine runs a configurable heartbeat loop so the system operates autonomously between user interactions.

**Meaning:** At each heartbeat interval (default hourly, user-configurable), COO checks and advances task progress, CFO runs financial health checks, CRO scans for new opportunities. Actions produced by heartbeat follow autonomy tiers: auto-execute for low-risk, queue for approval on high-risk. While the user is offline, only low-risk operations proceed; high-risk actions wait in a queue for the user's next session.

**Implication:** The user can disable heartbeat entirely for passive mode. Heartbeat is what makes Kompany a living company rather than a command-response tool.

## deployment
Initially runs as a local long-running process; containerized cloud deployment deferred to multi-tenant phase.

**Meaning:** `kompany serve` starts a background process with heartbeat scheduler. Users interact via CLI/API/SDK locally. Process crash triggers automatic restart and checkpoint recovery. No Docker needed at single-user stage — it adds complexity without benefit. Supports systemd/launchd registration for auto-start and crash recovery. Containerization and PostgreSQL migration happen together when multi-tenancy is needed.

**Implication:** Keep deployment simple. The system must be resilient to restarts through checkpoint-and-resume, not through infrastructure complexity.

## multi-tenancy
Multi-user support is deferred but architecturally unblocked.

**Meaning:** Current design is single-user, single-company. Each company already uses its own SQLite database file, which is natural tenant isolation. No global singletons or hardcoded paths — KompanyEngine accepts configuration at instantiation. Real multi-tenancy (user registration, authentication, billing) is deferred until a later phase.

**Implication:** Priority is making the single-user experience exceptional. Do not introduce multi-tenant complexity prematurely, but avoid design decisions that would block it later.

## backup
Automatic two-layer backup: local snapshots and optional remote storage.

**Meaning:** Local: engine copies the SQLite file as a timestamped snapshot on each heartbeat, retaining the most recent N copies (default 7 days), auto-cleaning older ones. Remote: user-configurable remote storage (S3, Google Drive, Dropbox) via adapter pattern, uploaded periodically by heartbeat. Backup scope: entire SQLite database (ledger, projects, memories, audit log, checkpoints) + soul files + user configuration. LLM provider credentials are excluded — user manages those separately. Restore via `kompany restore --from <backup_path>`, replacing the current database and resuming from checkpoint on restart. All backup logic is pure code, no LLM dependency.

**Implication:** Backup is infrastructure-level, same tier as checkpoint-and-resume. Must work even when LLM quota is exhausted.
