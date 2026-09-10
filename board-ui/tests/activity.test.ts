import { describe, expect, it } from 'vitest';
import { lineForEvent, lineFromReplay, mergeBackfill, pushLine, roleOf, totalSpend, RING_CAP } from '../src/studio/activity';

const now = new Date('2026-09-10T10:00:00Z');

describe('studio activity stream', () => {
  it('maps harness / spend / status / audit events and ignores others', () => {
    expect(lineForEvent({ type: 'harness.event', data: { agent_role: 'CMO', kind: 'tool_use', summary: 'web.search(...)' } }, now))
      .toMatchObject({ kind: 'tool', text: 'web.search(...)', source: 'live' });
    const spend = lineForEvent({ type: 'llm.spend', data: { agent_name: 'cmo', cost_usd: 0.0312, model: 'economy' } }, now);
    expect(spend).toMatchObject({ kind: 'spend', text: '$0.0312', cost: 0.0312, detail: 'economy' });
    expect(lineForEvent({ type: 'agent.activity', data: { agent_role: 'cmo', status: 'working', current_task: 'draft bio' } }, now))
      .toMatchObject({ kind: 'status', text: 'working', detail: 'draft bio' });
    expect(lineForEvent({ type: 'audit.tool_action.inline', data: { agent_role: 'cmo', action: 'ran web.search' } }, now)?.kind).toBe('tool');
    expect(lineForEvent({ type: 'audit.approval.approved', data: {} }, now)?.kind).toBe('approval');
    expect(lineForEvent({ type: 'daemon.tick', data: {} }, now)).toBeNull();
    expect(roleOf({ type: 'llm.spend', data: { agent_name: 'CFO' } })).toBe('cfo');
    expect(roleOf({ type: 'daemon.tick', data: {} })).toBeNull();
  });

  it('ring buffer caps and keeps the newest', () => {
    let buf: ReturnType<typeof pushLine> = [];
    for (let i = 0; i < RING_CAP + 20; i++) buf = pushLine(buf, { ts: `t${i}`, kind: 'text', text: `l${i}`, source: 'live' });
    expect(buf.length).toBe(RING_CAP);
    expect(buf[0]?.text).toBe('l20');
    expect(buf[buf.length - 1]?.text).toBe(`l${RING_CAP + 19}`);
  });

  it('merges replay with live lines, deduped and time-ordered', () => {
    const replay = [
      lineFromReplay({ ts: '2026-09-10T09:00:00', kind: 'tool', text: 'ran web.search', source: 'audit' }),
      lineFromReplay({ ts: '2026-09-10T09:00:05', kind: 'spend', text: '$0.031', source: 'ledger', detail: 'AI: cmo' }),
    ];
    const live = [
      { ts: '2026-09-10T09:00:00', kind: 'tool' as const, text: 'ran web.search', source: 'live' as const },
      { ts: '2026-09-10T09:00:09', kind: 'text' as const, text: 'drafting', source: 'live' as const },
    ];
    const merged = mergeBackfill(replay, live);
    expect(merged.map((l) => l.text)).toEqual(['ran web.search', '$0.031', 'drafting']);
    expect(totalSpend(merged)).toBeCloseTo(0.031);
  });
});
