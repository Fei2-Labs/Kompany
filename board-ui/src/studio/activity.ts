// Pure session-stream logic for Studio: map SSE events to lines, keep a
// bounded per-role ring buffer, merge server replay (/activity/{role}) with
// live lines. No React, no fetch — unit-tested in tests/activity.test.ts.

import type { SseEvent } from '../api/events';

export type LineKind = 'turn' | 'tool' | 'text' | 'spend' | 'status' | 'approval' | 'health' | 'audit' | 'session';

export interface Line {
  /** ISO timestamp (server) or receipt time (live). */
  ts: string;
  kind: LineKind;
  text: string;
  detail?: string;
  source: 'live' | 'audit' | 'ledger';
  /** Spend in USD when the line carries cost. */
  cost?: number;
  runId?: string | null;
  projectId?: string | null;
}

export const RING_CAP = 500;

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined;
}
function num(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
}

/** Role (lowercase) an SSE event belongs to, or null when it is not per-agent. */
export function roleOf(ev: SseEvent): string | null {
  const d = ev.data;
  const r = str(d.agent_role) ?? str(d.agent_name) ?? str(d.role);
  return r ? r.toLowerCase() : null;
}

const HARNESS_KIND: Record<string, LineKind> = {
  turn: 'turn', tool_use: 'tool', text: 'text', session_started: 'session', session: 'session', cost_delta: 'spend',
};

/** One SSE event → one stream line (or null when Studio does not show it). */
export function lineForEvent(ev: SseEvent, now: Date = new Date()): Line | null {
  const d = ev.data;
  const ts = str(d.timestamp) ?? str(d.ts) ?? now.toISOString();
  if (ev.type === 'harness.event') {
    const kind = HARNESS_KIND[str(d.kind) ?? ''] ?? 'text';
    const summary = str(d.summary) ?? str(d.kind) ?? 'working';
    return { ts, kind, text: summary, source: 'live', runId: str(d.run_id) ?? null, projectId: str(d.project_id) ?? null };
  }
  if (ev.type === 'llm.spend') {
    const cost = num(d.cost_usd) ?? num(d.amount_usd);
    const model = str(d.model) ?? '';
    const action = str(d.action_type) ?? '';
    return {
      ts, kind: 'spend', source: 'live', cost,
      text: cost !== undefined ? `$${cost.toFixed(4)}` : 'spend',
      detail: [model, action].filter(Boolean).join(' · ') || undefined,
      runId: str(d.run_id) ?? null, projectId: str(d.project_id) ?? null,
    };
  }
  if (ev.type === 'agent.activity') {
    const status = str(d.status) ?? 'idle';
    const task = str(d.current_task);
    return { ts, kind: 'status', source: 'live', text: status, detail: task, projectId: str(d.project_id) ?? null };
  }
  if (ev.type.startsWith('audit.')) {
    const et = ev.type.slice('audit.'.length);
    const kind: LineKind = et.startsWith('tool_') ? 'tool' : et.startsWith('approval') ? 'approval'
      : et.startsWith('health') ? 'health' : et.startsWith('workflow') ? 'turn' : 'audit';
    return { ts, kind, source: 'live', text: str(d.action) ?? et, detail: str(d.detail), runId: str(d.run_id) ?? null, projectId: str(d.project_id) ?? null };
  }
  return null;
}

/** Server replay row (GET /activity/{role}) → Line. */
export function lineFromReplay(row: Record<string, unknown>): Line {
  const kind = (str(row.kind) as LineKind | undefined) ?? 'audit';
  const text = str(row.text) ?? '';
  const cost = kind === 'spend' && text.startsWith('$') ? Number(text.slice(1)) : undefined;
  return {
    ts: str(row.ts) ?? '', kind, text, detail: str(row.detail),
    source: row.source === 'ledger' ? 'ledger' : 'audit',
    cost: Number.isFinite(cost) ? cost : undefined,
    runId: str(row.run_id) ?? null, projectId: str(row.project_id) ?? null,
  };
}

function key(l: Line): string {
  return `${l.ts}|${l.kind}|${l.text}`;
}

/** Append, dropping the oldest beyond RING_CAP. Returns a new array. */
export function pushLine(buffer: readonly Line[], line: Line, cap = RING_CAP): Line[] {
  const next = buffer.length >= cap ? buffer.slice(buffer.length - cap + 1) : buffer.slice();
  next.push(line);
  return next;
}

/** Replay lines first, then live lines not already present; time-ordered; capped. */
export function mergeBackfill(replay: readonly Line[], live: readonly Line[], cap = RING_CAP): Line[] {
  const seen = new Set(replay.map(key));
  const merged = replay.slice();
  for (const l of live) {
    const k = key(l);
    if (!seen.has(k)) { seen.add(k); merged.push(l); }
  }
  merged.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
  return merged.length > cap ? merged.slice(merged.length - cap) : merged;
}

/** Sum of spend lines (USD). */
export function totalSpend(lines: readonly Line[]): number {
  let t = 0;
  for (const l of lines) if (l.cost) t += l.cost;
  return t;
}

export const KIND_GLYPH: Record<LineKind, string> = {
  turn: '↻', tool: '⚙', text: '“', spend: '$', status: '●', approval: '!', health: '♥', audit: '·', session: '▶',
};
