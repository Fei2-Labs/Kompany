import { describe, expect, it } from 'vitest';
import { rowForEvent } from '../src/timeline/timeline';
import type { SseEvent } from '../src/api/events';

const NOW = 1_700_000_000_000;

function ev(type: string, data: Record<string, unknown>): SseEvent {
  return { type, data };
}

describe('rowForEvent (SSE → timeline row)', () => {
  it('maps llm.spend to a spend row with cost + model', () => {
    const r = rowForEvent(ev('llm.spend', { cost_usd: 0.0123, model: 'gpt', action_type: 'plan' }), NOW);
    expect(r?.tone).toBe('spend');
    expect(r?.summary).toContain('plan');
    expect(r?.detail).toContain('0.0123');
  });

  it('maps harness.event using agent_role + summary', () => {
    const r = rowForEvent(ev('harness.event', { agent_role: 'engineer', kind: 'code', summary: 'wrote module' }), NOW);
    expect(r?.tone).toBe('harness');
    expect(r?.actor).toBe('engineer');
    expect(r?.summary).toBe('wrote module');
  });

  it('maps daemon.tick with outcome + actions', () => {
    const r = rowForEvent(ev('daemon.tick', { outcome: 'ok', actions: ['a', 'b'] }), NOW);
    expect(r?.tone).toBe('tick');
    expect(r?.summary).toContain('ok');
    expect(r?.detail).toBe('a, b');
  });

  it('maps episode.recorded', () => {
    const r = rowForEvent(ev('episode.recorded', { summary: 'shipped', project_id: 'p1' }), NOW);
    expect(r?.tone).toBe('episode');
    expect(r?.detail).toBe('shipped');
  });

  it('maps self_update.event', () => {
    const r = rowForEvent(ev('self_update.event', { kind: 'patch', summary: 'bumped dep' }), NOW);
    expect(r?.tone).toBe('self-update');
    expect(r?.summary).toBe('bumped dep');
  });

  it('maps any audit.* subtype using action + agent_role', () => {
    const r = rowForEvent(ev('audit.approval.approved', { agent_role: 'ceo', action: 'Approved topup', detail: 'x' }), NOW);
    expect(r?.tone).toBe('audit');
    expect(r?.actor).toBe('ceo');
    expect(r?.summary).toBe('Approved topup');
  });

  it('falls back for unknown types rather than dropping', () => {
    const r = rowForEvent(ev('mystery.kind', {}), NOW);
    expect(r?.tone).toBe('other');
    expect(r?.summary).toBe('mystery.kind');
  });

  it('uses the injected timestamp', () => {
    const r = rowForEvent(ev('daemon.tick', { outcome: 'ok' }), NOW);
    expect(r?.receivedAt).toBe(NOW);
  });
});
