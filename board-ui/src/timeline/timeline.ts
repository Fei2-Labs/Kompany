// Pure SSE-event → timeline-row mapping. No React/fetch so the rendering of
// interleaved human + agent events is unit-testable in isolation.
//
// Event types (research §5): audit.<event_type>, harness.event, llm.spend,
// daemon.tick, self_update.event, episode.recorded.

import type { SseEvent } from '../api/events';

/** A renderable timeline row. */
export interface TimelineRow {
  id: number;
  /** Leading glyph (text, keeps the bundle lean — matches nav glyph style). */
  icon: string;
  /** Who/what acted: an agent role, "system", "daemon", "CEO", … */
  actor: string;
  /** One-line summary. */
  summary: string;
  /** Optional detail line (truncated). */
  detail?: string;
  /** Coarse tone for styling. */
  tone: 'audit' | 'harness' | 'spend' | 'tick' | 'self-update' | 'episode' | 'other';
  /** Wall-clock receipt time (ms) — SSE carries no authoritative ts on most events. */
  receivedAt: number;
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined;
}

function num(v: unknown): number | undefined {
  return typeof v === 'number' ? v : undefined;
}

function truncate(s: string, max = 160): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

let rowSeq = 0;
function nextRowId(): number {
  rowSeq += 1;
  return rowSeq;
}

/**
 * Map one parsed SSE event to a timeline row, or `null` for events the timeline
 * ignores. `now` is injectable for deterministic tests.
 */
export function rowForEvent(ev: SseEvent, now = Date.now()): TimelineRow | null {
  const d = ev.data;
  const base = { id: nextRowId(), receivedAt: now };

  if (ev.type === 'llm.spend') {
    const cost = num(d.cost_usd);
    const model = str(d.model) ?? 'model';
    const action = str(d.action_type) ?? 'llm';
    return {
      ...base,
      icon: '$',
      actor: 'spend',
      tone: 'spend',
      summary: `LLM spend · ${action}`,
      detail:
        cost !== undefined
          ? `$${cost.toFixed(4)} · ${model}`
          : model,
    };
  }

  if (ev.type === 'daemon.tick') {
    const outcome = str(d.outcome) ?? 'tick';
    const actions = Array.isArray(d.actions) ? (d.actions as unknown[]) : [];
    return {
      ...base,
      icon: '◷',
      actor: 'daemon',
      tone: 'tick',
      summary: `Daemon tick · ${outcome}`,
      detail: actions.length > 0 ? truncate(actions.join(', ')) : undefined,
    };
  }

  if (ev.type === 'harness.event') {
    const role = str(d.agent_role) ?? 'agent';
    const kind = str(d.kind) ?? 'work';
    const summary = str(d.summary) ?? kind;
    return {
      ...base,
      icon: '◆',
      actor: role,
      tone: 'harness',
      summary: truncate(summary),
      detail: kind !== summary ? kind : undefined,
    };
  }

  if (ev.type === 'self_update.event') {
    return {
      ...base,
      icon: '⟳',
      actor: 'self-update',
      tone: 'self-update',
      summary: truncate(str(d.summary) ?? str(d.kind) ?? 'self-update'),
    };
  }

  if (ev.type === 'episode.recorded') {
    return {
      ...base,
      icon: '✓',
      actor: 'learning',
      tone: 'episode',
      summary: 'Episode recorded',
      detail: truncate(str(d.summary) ?? str(d.project_id) ?? ''),
    };
  }

  if (ev.type.startsWith('audit.')) {
    const role = str(d.agent_role) ?? 'system';
    const action = str(d.action) ?? ev.type.replace(/^audit\./, '');
    const detail = str(d.detail);
    return {
      ...base,
      icon: '•',
      actor: role,
      tone: 'audit',
      summary: truncate(action),
      detail: detail ? truncate(detail) : undefined,
    };
  }

  // Unknown event type — surface it rather than silently dropping so the
  // founder still sees activity. (Never throw on a malformed frame.)
  return {
    ...base,
    icon: '·',
    actor: 'event',
    tone: 'other',
    summary: truncate(ev.type),
  };
}
