// Thin typed fetch client over the existing Kompany REST routes. The Vite
// dev server proxies these to the running engine (127.0.0.1:8000); in the
// bundled app they are same-origin. No state, no caching — callers refetch
// on SSE triggers (see events.ts).

import type {
  AgentStatus,
  ApprovalRequest,
  ChannelSession,
  ChannelTurn,
  CompanyStatus,
  DirectiveResult,
  EpisodeRow,
  LlmSpendSummary,
  ObservabilitySnapshot,
  ProjectDetail,
  ProjectListItem,
  RunCost,
  RuntimeState,
} from './types';
import {
  buildApprovePayload,
  buildCancelPayload,
  buildCommentPayload,
  buildRejectPayload,
  buildRevisePayload,
  buildSnoozePayload,
  type ApproveInput,
  type CancelInput,
  type CommentInput,
  type RejectInput,
  type ReviseInput,
  type SnoozeInput,
} from './approvalPayloads';

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      signal,
      headers: { accept: 'application/json' },
    });
  } catch (err) {
    // Network / connection refused (engine not running).
    throw new ApiError(0, err instanceof Error ? err.message : 'network error');
  }
  if (!res.ok) {
    throw new ApiError(res.status, `${path} → ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

async function postJson<T>(
  path: string,
  body?: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: 'POST',
      signal,
      headers: {
        accept: 'application/json',
        'content-type': 'application/json',
      },
      // An omitted body is sent as `{}` so optional-body routes (approve)
      // still parse; the engine treats `{}` as all-defaults.
      body: JSON.stringify(body ?? {}),
    });
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : 'network error');
  }
  if (!res.ok) {
    throw new ApiError(res.status, `${path} → ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

/** `GET /projects` — active projects only. */
export function getProjects(signal?: AbortSignal): Promise<ProjectListItem[]> {
  return getJson<ProjectListItem[]>('/projects', signal);
}

/** `GET /projects?include_draft=1` — active + draft + completed/cancelled rows. */
export function getProjectsIncludingDraft(
  signal?: AbortSignal,
): Promise<ProjectListItem[]> {
  return getJson<ProjectListItem[]>('/projects?include_draft=1', signal);
}

/** `GET /projects/{id}` — detail with task rows + assigned agents. */
export function getProject(
  id: string,
  signal?: AbortSignal,
): Promise<ProjectDetail> {
  return getJson<ProjectDetail>(`/projects/${encodeURIComponent(id)}`, signal);
}

/** `GET /inbox` — pending + snoozed approvals, newest first. */
export function getInbox(signal?: AbortSignal): Promise<ApprovalRequest[]> {
  return getJson<ApprovalRequest[]>('/inbox', signal);
}

/** `GET /episodes` — retrospective rows (payload stripped). */
export function getEpisodes(signal?: AbortSignal): Promise<EpisodeRow[]> {
  return getJson<EpisodeRow[]>('/episodes', signal);
}

/** `GET /status` — LLM-free metrics strip source. */
export function getStatus(signal?: AbortSignal): Promise<CompanyStatus> {
  return getJson<CompanyStatus>('/status', signal);
}

// ---------------------------------------------------------------------------
// Runtime control (header strip) + read-only org / usage panes.
// ---------------------------------------------------------------------------

/** `GET /runtime` — persisted engine runtime state (running | suspended). */
export function getRuntime(signal?: AbortSignal): Promise<RuntimeState> {
  return getJson<RuntimeState>('/runtime', signal);
}

/** `POST /runtime/suspend` — `{ reason }`. Idempotent on the engine. */
export function suspendRuntime(reason: string): Promise<RuntimeState> {
  return postJson<RuntimeState>('/runtime/suspend', { reason });
}

/** `POST /runtime/resume`. Idempotent on the engine. */
export function resumeRuntime(): Promise<RuntimeState> {
  return postJson<RuntimeState>('/runtime/resume');
}

/** `POST /heartbeat` — run one heartbeat check (dry-run, no dispatch). */
export function runHeartbeat(): Promise<Record<string, unknown>> {
  return postJson<Record<string, unknown>>('/heartbeat');
}

/** `GET /agents/status` — read-only org view (11 C-suite roles, in order). */
export function getAgents(signal?: AbortSignal): Promise<AgentStatus[]> {
  return getJson<AgentStatus[]>('/agents/status', signal);
}

/** `GET /llm/spend/summary` — cumulative AI cost (Usage pane). */
export function getSpendSummary(signal?: AbortSignal): Promise<LlmSpendSummary> {
  return getJson<LlmSpendSummary>('/llm/spend/summary', signal);
}

/** `GET /observability` — operational snapshot (Autopilot recent ticks). */
export function getObservability(
  signal?: AbortSignal,
): Promise<ObservabilitySnapshot> {
  return getJson<ObservabilitySnapshot>('/observability', signal);
}

// ---------------------------------------------------------------------------
// Approval actions (`/approvals/{id}/*`). Each returns the resolved approval
// dict; HARNESS_EFFECT_ACTIONS approvals add `effect` (+ `tool_result` for
// `tool_action`). We type the response loosely as a record — the caller reads
// the optional `effect`/`tool_result` keys when present.
// ---------------------------------------------------------------------------

/** Resolution response. `effect`/`tool_result` present for HARNESS_EFFECT_ACTIONS. */
export type ApprovalResult = Record<string, unknown> & {
  effect?: unknown;
  tool_result?: unknown;
};

function approvalPath(id: string, action: string): string {
  return `/approvals/${encodeURIComponent(id)}/${action}`;
}

/** `POST /approvals/{id}/approve` — `{ comment? }`. */
export function approveApproval(
  id: string,
  input: ApproveInput = {},
): Promise<ApprovalResult> {
  return postJson<ApprovalResult>(approvalPath(id, 'approve'), buildApprovePayload(input));
}

/** `POST /approvals/{id}/reject` — `{ reason, comment? }`. */
export function rejectApproval(id: string, input: RejectInput): Promise<ApprovalResult> {
  return postJson<ApprovalResult>(approvalPath(id, 'reject'), buildRejectPayload(input));
}

/** `POST /approvals/{id}/revise` — `{ counter, comment? }`. */
export function reviseApproval(id: string, input: ReviseInput): Promise<ApprovalResult> {
  return postJson<ApprovalResult>(approvalPath(id, 'revise'), buildRevisePayload(input));
}

/** `POST /approvals/{id}/snooze` — `{ minutes, comment? }`. */
export function snoozeApproval(id: string, input: SnoozeInput): Promise<ApprovalResult> {
  return postJson<ApprovalResult>(approvalPath(id, 'snooze'), buildSnoozePayload(input));
}

/** `POST /approvals/{id}/cancel` — `{ reason?, comment? }`. */
export function cancelApproval(
  id: string,
  input: CancelInput = {},
): Promise<ApprovalResult> {
  return postJson<ApprovalResult>(approvalPath(id, 'cancel'), buildCancelPayload(input));
}

/** `POST /approvals/{id}/comment` — `{ body, by_type, by_id? }`. */
export function commentApproval(id: string, input: CommentInput): Promise<ApprovalResult> {
  return postJson<ApprovalResult>(approvalPath(id, 'comment'), buildCommentPayload(input));
}

// ---------------------------------------------------------------------------
// CEO channel (`/channel/*`) + the fire-and-forget `/directive` fallback.
// ---------------------------------------------------------------------------

/** `POST /channel/send` — `{ text, session_id? }`. Multi-turn CEO session. */
export function channelSend(
  text: string,
  sessionId?: string | null,
): Promise<DirectiveResult> {
  const body: Record<string, unknown> = { text };
  if (sessionId) body.session_id = sessionId;
  return postJson<DirectiveResult>('/channel/send', body);
}

/** `POST /channel/sessions/{id}/go` — execute a gated/proposed session. */
export function channelGo(sessionId: string): Promise<DirectiveResult> {
  return postJson<DirectiveResult>(
    `/channel/sessions/${encodeURIComponent(sessionId)}/go`,
  );
}

/** `POST /channel/sessions/{id}/abandon` — close without executing. */
export function channelAbandon(sessionId: string): Promise<DirectiveResult> {
  return postJson<DirectiveResult>(
    `/channel/sessions/${encodeURIComponent(sessionId)}/abandon`,
  );
}

/** `GET /channel/sessions/{id}` — session + ordered turns (reload restore). */
export function getChannelSession(
  sessionId: string,
  signal?: AbortSignal,
): Promise<{ session: ChannelSession; turns: ChannelTurn[] }> {
  return getJson(`/channel/sessions/${encodeURIComponent(sessionId)}`, signal);
}

/** `GET /channel/runs/{run_id}/cost` — authoritative per-run AI cost. */
export function getRunCost(runId: string, signal?: AbortSignal): Promise<RunCost> {
  return getJson<RunCost>(`/channel/runs/${encodeURIComponent(runId)}/cost`, signal);
}

/**
 * `POST /directive` — fire-and-forget simple directive (no session thread).
 * Returns the same flattened DirectiveResult; we keep this for a "quick send"
 * path that doesn't open a multi-turn CEO session.
 */
export function sendDirective(text: string): Promise<DirectiveResult> {
  return postJson<DirectiveResult>('/directive', { text });
}

// ---- Integrations / Settings ---------------------------------------------
// Telegram + (future) Resend/Email SMTP credential management. The engine
// verifies-then-stores tokens in the encrypted vault; these calls are the
// board SPA's counterpart to the legacy /ui/settings.html forms.

export interface TelegramCredentials {
  telegram_bot_token: string;
  telegram_bot_token_set: boolean;
  telegram_bot_token_mask?: string;
  telegram_allowed_chat_ids: string;
}

export interface ConnectResult {
  ok: boolean;
  detail: string;
}

/** `GET /integrations/telegram/credentials` — masked token + chat ids. */
export function getTelegramCredentials(
  signal?: AbortSignal,
): Promise<TelegramCredentials> {
  return getJson<TelegramCredentials>('/integrations/telegram/credentials', signal);
}

/**
 * `POST /integrations/telegram/connect` — verify token via getMe, then
 * store. Empty `bot_token` keeps the saved one (mirror Resend pattern).
 */
export function connectTelegram(input: {
  bot_token: string;
  allowed_chat_ids: string;
}): Promise<ConnectResult> {
  return postJson<ConnectResult>('/integrations/telegram/connect', input);
}
