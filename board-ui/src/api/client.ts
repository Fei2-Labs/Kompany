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

/** Same contract as `postJson` but for `PUT` (merge-set settings routes). */
async function putJson<T>(
  path: string,
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: 'PUT',
      signal,
      headers: {
        accept: 'application/json',
        'content-type': 'application/json',
      },
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : 'network error');
  }
  if (!res.ok) {
    throw new ApiError(res.status, `${path} → ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

/** `DELETE` with no body — vault credential removal / workspace teardown. */
async function deleteJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: 'DELETE',
      signal,
      headers: { accept: 'application/json' },
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

// ---- Browser CDP config ---------------------------------------------------

export interface BrowserConfig {
  cdp_endpoint: string;
  connected: boolean;
  browser_type: string | null;
  playwright_installed: boolean;
}

export interface BrowserProbeResult {
  browsers: Array<{ port: number; endpoint: string; browser_type: string | null }>;
}

/** `GET /browser/config` — current CDP endpoint + connection status. */
export function getBrowserConfig(
  signal?: AbortSignal,
): Promise<BrowserConfig> {
  return getJson<BrowserConfig>('/browser/config', signal);
}

/** `POST /browser/config` — persist the CDP endpoint. */
export function setBrowserConfig(
  cdp_endpoint: string,
): Promise<ConnectResult & { cdp_endpoint?: string; connected?: boolean; browser_type?: string | null }> {
  return postJson('/browser/config', { cdp_endpoint });
}

/** `GET /browser/probe` — auto-detect running browsers on common ports. */
export function probeBrowsers(
  signal?: AbortSignal,
): Promise<BrowserProbeResult> {
  return getJson<BrowserProbeResult>('/browser/probe', signal);
}

/** `GET /channels/status` — Telegram/email adapter health + outbox counts. */
export interface ChannelsStatus {
  telegram: {
    configured: boolean;
    running: boolean;
    last_update_at: string | null;
    updates_handled: number;
  };
  email: {
    configured: boolean;
    poll_every_ticks: number;
    last_poll_at: string | null;
  };
  outbox: { enabled: boolean; counts: Record<string, number> };
}
export function getChannelsStatus(signal?: AbortSignal): Promise<ChannelsStatus> {
  return getJson<ChannelsStatus>('/channels/status', signal);
}

// ---- LLM model -------------------------------------------------------------
// All three tiers (apex/primary/economy) share one model. Switching applies
// immediately on the live engine, no restart.

export interface ModelSetting {
  current_model: string;
  provider: string;
  base_url: string;
  available_models: string[];
  error: string;
}

/** `GET /settings/model` — current model + the endpoint's advertised list. */
export function getModelSetting(signal?: AbortSignal): Promise<ModelSetting> {
  return getJson<ModelSetting>('/settings/model', signal);
}

/** `POST /settings/model` — `{ model }`. Applies to the live engine now. */
export function setModelSetting(model: string): Promise<ModelSetting> {
  return postJson<ModelSetting>('/settings/model', { model });
}

// ---- Model source (subscription vs custom API key) ------------------------

export interface ModelSource {
  kind: 'custom_api' | 'claude_subscription' | 'openai_subscription' | string;
  billing_mode?: string | null;
  monthly_fee_usd?: number | null;
  price_overrides?: Record<string, [number, number]> | null;
  vehicle?: string;
  execution_summary?: string;
}

/** `GET /settings/model-source` — active source, or `null` (legacy billing). */
export function getModelSource(
  signal?: AbortSignal,
): Promise<ModelSource | null> {
  return getJson<ModelSource | null>('/settings/model-source', signal);
}

/**
 * `PUT /settings/model-source` — set or clear (`kind: null`) the source.
 * No `vehicle` input — the engine derives the execution loop from `kind`.
 */
export function setModelSource(body: {
  kind: string | null;
  monthly_fee_usd?: number;
}): Promise<ModelSource> {
  return putJson<ModelSource>('/settings/model-source', body);
}

export interface DetectedCli {
  found: boolean;
  version?: string;
}

/** `GET /settings/detect-clis` — probes PATH for agent CLIs (claude/codex/opencode). */
export function detectAgentClis(
  signal?: AbortSignal,
): Promise<Record<string, DetectedCli>> {
  return getJson<Record<string, DetectedCli>>('/settings/detect-clis', signal);
}

// ---- Email: Resend (recommended) + SMTP (alternative) --------------------

export interface ResendCredentials {
  resend_api_key_set?: boolean;
  resend_api_key_mask?: string;
  resend_from?: string;
}

/** `GET /integrations/resend/credentials` — masked key + saved From. */
export function getResendCredentials(
  signal?: AbortSignal,
): Promise<ResendCredentials> {
  return getJson<ResendCredentials>('/integrations/resend/credentials', signal);
}

/**
 * `POST /integrations/resend/connect` — verify (list domains) then store.
 * Empty `api_key` keeps the saved one.
 */
export function connectResend(input: {
  api_key: string;
  resend_from: string;
}): Promise<ConnectResult> {
  return postJson<ConnectResult>('/integrations/resend/connect', input);
}

export interface EmailSmtpCredentials {
  smtp_host?: string;
  smtp_port?: string;
  smtp_user?: string;
  smtp_password_set?: boolean;
  smtp_password_mask?: string;
  smtp_from?: string;
}

/** `GET /integrations/email_smtp/credentials` — masked password + saved fields. */
export function getEmailSmtpCredentials(
  signal?: AbortSignal,
): Promise<EmailSmtpCredentials> {
  return getJson<EmailSmtpCredentials>(
    '/integrations/email_smtp/credentials',
    signal,
  );
}

/** `POST /integrations/email/connect` — SMTP login-verify then store. */
export function connectEmailSmtp(input: {
  smtp_host: string;
  smtp_port: string;
  smtp_user: string;
  smtp_password: string;
  smtp_from: string;
}): Promise<ConnectResult> {
  return postJson<ConnectResult>('/integrations/email/connect', input);
}

/**
 * `POST /integrations/email/test` — send a real test email via whichever
 * provider (Resend/SMTP) is connected. Empty `to` defaults to the
 * connected From address.
 */
export function sendTestEmail(to: string): Promise<ConnectResult> {
  return postJson<ConnectResult>('/integrations/email/test', { to });
}

// ---- Founder profile + rules -----------------------------------------------

export interface FounderProfile {
  address?: string;
  pronouns?: string;
  comms_style?: string;
  language?: string;
  working_hours?: string;
  timezone?: string;
  risk_tolerance?: string;
}

/** `GET /founder/profile` — or `null` when unset. */
export function getFounderProfile(
  signal?: AbortSignal,
): Promise<FounderProfile | null> {
  return getJson<FounderProfile | null>('/founder/profile', signal);
}

/** `PUT /founder/profile` — partial merge; `{ clear: true }` removes it. */
export function setFounderProfile(
  body: Partial<FounderProfile> & { clear?: boolean },
): Promise<FounderProfile> {
  return putJson<FounderProfile>('/founder/profile', body);
}

export type FounderRuleKind =
  | 'exclude_capability'
  | 'budget_cap'
  | 'forbid_paid_category';

export interface FounderHardRule {
  kind: FounderRuleKind;
  match: string;
  action: string;
}

export interface FounderRules {
  hard: FounderHardRule[];
  soft: string;
}

/** `GET /founder/rules` — `{ hard, soft }`, or `null` when unset. */
export function getFounderRules(
  signal?: AbortSignal,
): Promise<FounderRules | null> {
  return getJson<FounderRules | null>('/founder/rules', signal);
}

/** `PUT /founder/rules` — full replace of `{ hard, soft }`; `{ clear: true }` removes both. */
export function setFounderRules(
  body: Partial<FounderRules> & { clear?: boolean },
): Promise<FounderRules> {
  return putJson<FounderRules>('/founder/rules', body);
}

// ---- Integrations registry + credentials vault ----------------------------

export interface IntegrationInfo {
  integration_id: string;
  display_name: string;
  description: string;
  required_credentials: string[];
  connected: boolean;
  tools: string[];
}

/** `GET /integrations` — every registered integration + connection state. */
export function getIntegrations(
  signal?: AbortSignal,
): Promise<IntegrationInfo[]> {
  return getJson<IntegrationInfo[]>('/integrations', signal);
}

export interface CredentialEntry {
  name: string;
  configured: boolean;
  updated_at?: string;
}

/** `GET /credentials` — raw vault entries (values never returned). */
export function getCredentials(
  signal?: AbortSignal,
): Promise<CredentialEntry[]> {
  return getJson<CredentialEntry[]>('/credentials', signal);
}

/** `POST /credentials` — `{ name, value }`. Overwrites any existing entry. */
export function setCredential(name: string, value: string): Promise<CredentialEntry> {
  return postJson<CredentialEntry>('/credentials', { name, value });
}

/** `DELETE /credentials/{name}`. */
export function deleteCredential(name: string): Promise<{ ok?: boolean }> {
  return deleteJson<{ ok?: boolean }>(`/credentials/${encodeURIComponent(name)}`);
}

/** `POST /credentials/rotate-key` — re-encrypts every entry with a new Fernet key. */
export function rotateCredentialKey(
  newVaultKey: string,
): Promise<{ rotated?: number }> {
  return postJson<{ rotated?: number }>('/credentials/rotate-key', {
    new_vault_key: newVaultKey,
  });
}

// ---- Workspaces (one isolated data dir per brand) --------------------------

export interface WorkspaceEntry {
  name: string;
  label: string;
  data_dir: string;
  active: boolean;
}

export interface WorkspacesList {
  active: string;
  env_override: boolean;
  workspaces: WorkspaceEntry[];
}

/** `GET /workspaces` — registry: active brand + every registered entry. */
export function getWorkspaces(signal?: AbortSignal): Promise<WorkspacesList> {
  return getJson<WorkspacesList>('/workspaces', signal);
}

/** `POST /workspaces/switch` — `{ name }`. Rebinds the server; caller reloads. */
export function switchWorkspace(
  name: string,
): Promise<WorkspaceEntry & { error?: string; restart_required?: boolean }> {
  return postJson<WorkspaceEntry & { error?: string; restart_required?: boolean }>(
    '/workspaces/switch',
    { name },
  );
}

/** `POST /workspaces` — `{ name, label? }`. Registers, does not switch. */
export function createWorkspace(
  name: string,
  label = '',
): Promise<WorkspaceEntry & { error?: string }> {
  return postJson<WorkspaceEntry & { error?: string }>('/workspaces', {
    name,
    label,
  });
}
