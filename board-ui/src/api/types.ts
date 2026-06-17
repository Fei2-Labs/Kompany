// API shapes. These mirror the EXACT JSON returned by the Kompany engine
// REST routes — see research/project-state-and-endpoints.md. Do not add
// fields the backend does not emit; the board derives everything else.

/** ProjectStatus enum (state/models.py:44). `paused`/`failed` have no
 *  project-level writer in practice — kept for completeness only. */
export type ProjectStatus =
  | 'draft'
  | 'active'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';

/** ProjectType enum (state/models.py). */
export type ProjectType = 'revenue' | 'operational' | 'strategic';

/** TaskStatus enum (state/models.py:81). `delivered`/`blocked` are the
 *  founder-action triggers surfaced in Needs-You. */
export type TaskStatus =
  | 'pending'
  | 'active'
  | 'completed'
  | 'delivered'
  | 'blocked'
  | 'failed'
  | 'cancelled';

/** ApprovalStatus enum (state/models.py:96). `snoozed` is NOT terminal. */
export type ApprovalStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'revision_requested'
  | 'snoozed'
  | 'cancelled';

export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical';

/** `GET /projects` row (and the shared first six fields of `/projects/{id}`).
 *  With `?include_draft=1`, `type`/`status` are raw strings, not enum-coerced. */
export interface ProjectListItem {
  id: string;
  name: string;
  type: string;
  status: string;
  target_amount: number | null;
  funded_amount: number;
}

/** A task row inside `GET /projects/{id}`. */
export interface ProjectTask {
  id: string;
  title: string;
  agent: string;
  status: string;
}

/** `GET /projects/{id}` — the six list fields plus the detail block. */
export interface ProjectDetail extends ProjectListItem {
  plan: Record<string, unknown>;
  assigned_agents: string[];
  tasks: ProjectTask[];
}

/** Approval card — `GET /inbox` rows (ApprovalRequest.model_dump + comment_count). */
export interface ApprovalRequest {
  id: string;
  status: ApprovalStatus;
  action_type: string;
  summary: string;
  payload: Record<string, unknown>;
  directive_id: string | null;
  project_id: string | null;
  requested_by: string | null;
  resolved_by: string | null;
  resolution_reason: string | null;
  created_at: string;
  resolved_at: string | null;
  severity: Severity;
  predecessor_id: string | null;
  snoozed_until: string | null;
  snoozed_by: string | null;
  /** Present on `/inbox` rows only. */
  comment_count?: number;
}

/** `GET /episodes` list row (payload_json stripped in list view). */
export interface EpisodeRow {
  project_id: string;
  summary: string;
  retention_tier: 'full' | 'summary';
  run_id: string | null;
  created_at: string;
  updated_at: string;
}

/** Ticker block inside `GET /status`. */
export interface TickerBlock {
  running: boolean;
  last_tick_at: string | null;
  tick_count: number;
  interval_seconds: number;
}

/**
 * Flattened `DirectiveResult` returned by `/channel/send`, `/directive`,
 * `/channel/sessions/{id}/go` and `/abandon` (channel.py `DirectiveResult.to_dict()`).
 *
 * `status` drives the CEO-channel state machine:
 *   - `clarify`              → CEO asks back; reply on the SAME session_id.
 *   - `gated` | `proposed`   → awaiting founder GO; hit `/go` or `/abandon`.
 *   - `dispatched` | `answered` | `failed` → terminal.
 * On `failed`, `error_code` is a classified, founder-safe code (never a raw 500).
 */
export interface DirectiveResult {
  status: string;
  message: string;
  project_id: string | null;
  approval_id: string | null;
  total_ai_cost: number;
  agents_used: string[];
  run_id: string | null;
  session_id: string | null;
  error_code?: string;
}

/** Classified LLM failure codes (channel.py `_classify_ping_error`). */
export type DirectiveErrorCode =
  | 'rate_limited'
  | 'unauthorized'
  | 'network'
  | 'provider_error';

/** A single CEO-channel turn (`_turn_to_dict`, channel.py). */
export interface ChannelTurn {
  turn_index: number;
  role: 'founder' | 'ceo';
  content: string;
  kind: 'message' | 'clarify_question' | 'preview' | 'final' | 'progress_summary';
  cost: number;
  run_id: string | null;
  directive_id: string | null;
  created_at: string | null;
}

/** A CEO-channel session row (`_session_to_dict`, channel.py). */
export interface ChannelSession {
  session_id: string;
  state: string;
  route: string;
  clarify_turns: number;
  created_at: string | null;
  closed_at: string | null;
  run_id: string | null;
  directive_id: string | null;
  project_id: string | null;
  approval_id: string | null;
}

/** `GET /channel/runs/{run_id}/cost` — reconcile source after reload. */
export interface RunCost {
  run_id: string;
  total_cost: number;
}

/** A daemon tick row inside `/observability.recent_ticks` (core/ticker.py). */
export interface TickRow {
  outcome?: string;
  actions?: string[];
  duration_ms?: number;
  activity_kind?: string;
  created_at?: string;
  [k: string]: unknown;
}

/** Subset of `GET /observability` the Autopilot pane reads (observe.py). */
export interface ObservabilitySnapshot {
  status: string;
  runtime: RuntimeState;
  recent_ticks: TickRow[];
  [k: string]: unknown;
}

/** `GET /runtime` — persisted engine runtime state (state/runtime.py). */
export interface RuntimeState {
  /** `"running" | "suspended"`. */
  state: string;
  reason: string | null;
  /** ISO timestamp of the last transition, or null. */
  since: string | null;
}

/** `GET /agents/status` row — always 11 canonical C-suite roles, in order. */
export interface AgentStatus {
  /** Upper-cased role label (e.g. `"CEO"`). */
  role: string;
  /** `"idle" | "busy" | …` (free string). */
  status: string;
  last_action: string | null;
  latency_ms: number | null;
  activity_kind: string;
  project_id: string | null;
  project_type: string | null;
}

/** `GET /llm/spend/summary` — cumulative AI cost chip source. */
export interface LlmSpendSummary {
  total_usd: number;
  row_count: number;
}

/** `GET /status` — the LLM-free metrics-strip source (core/status_ops.py). */
export interface CompanyStatus {
  company: string;
  goal: string;
  time_horizon: string;
  exclusions: string;
  stage: string;
  balance: number;
  total_income: number;
  total_expenses: number;
  total_ai_costs: number;
  active_projects: number;
  virtual_days_elapsed: number;
  virtual_days_budget: number;
  virtual_days_remaining: number;
  ticker: TickerBlock;
}
