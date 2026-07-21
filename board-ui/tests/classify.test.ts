import { describe, expect, it } from 'vitest';
import {
  buildBacklog,
  buildDone,
  buildInFlight,
  buildNeedsYou,
  classifyAction,
  severityRank,
} from '../src/board/classify';
import type {
  ApprovalRequest,
  EpisodeRow,
  ProjectDetail,
  ProjectListItem,
} from '../src/api/types';

function approval(over: Partial<ApprovalRequest>): ApprovalRequest {
  return {
    id: 'a1',
    status: 'pending',
    action_type: 'debate_decision',
    summary: 'summary',
    payload: {},
    directive_id: null,
    project_id: null,
    requested_by: null,
    resolved_by: null,
    resolution_reason: null,
    created_at: '2026-06-15T00:00:00Z',
    resolved_at: null,
    severity: 'medium',
    predecessor_id: null,
    snoozed_until: null,
    snoozed_by: null,
    comment_count: 0,
    ...over,
  };
}

function project(over: Partial<ProjectListItem>): ProjectListItem {
  return {
    id: 'p1',
    name: 'Proj',
    type: 'revenue',
    status: 'active',
    target_amount: 1000,
    funded_amount: 250,
    ...over,
  };
}

describe('classifyAction (action_type → badge)', () => {
  it.each(['envelope_topup', 'budget_increase', 'tool_action'])(
    'maps %s → money',
    (t) => {
      expect(classifyAction(t)).toBe('money');
    },
  );

  it.each([
    'delivery_approval',
    'channel_post',
    'deai_gate',
    'fabrication_check',
    'outward_park',
    'csuite_review',
  ])('maps %s → ship-gate', (t) => {
    expect(classifyAction(t)).toBe('ship-gate');
  });

  it.each([
    'debate_decision',
    'target_feasibility',
    'glossary_review',
    'override',
    'self_update_proposal',
    'totally_unknown_type',
  ])('maps %s → decision (default)', (t) => {
    expect(classifyAction(t)).toBe('decision');
  });
});

describe('severityRank', () => {
  it('orders critical > high > medium > low > info', () => {
    expect(severityRank('critical')).toBeGreaterThan(severityRank('high'));
    expect(severityRank('high')).toBeGreaterThan(severityRank('medium'));
    expect(severityRank('medium')).toBeGreaterThan(severityRank('low'));
    expect(severityRank('low')).toBeGreaterThan(severityRank('info'));
  });
  it('treats unknown severities as lowest', () => {
    expect(severityRank('garbage')).toBe(0);
  });
});

describe('buildNeedsYou', () => {
  it('keeps only pending/snoozed approvals and badges them', () => {
    const cards = buildNeedsYou(
      [
        approval({ id: 'm', action_type: 'envelope_topup', severity: 'low' }),
        approval({ id: 'g', action_type: 'delivery_approval', severity: 'critical' }),
        approval({ id: 'done', status: 'approved' }),
      ],
      [],
    );
    expect(cards.map((c) => c.id)).toEqual(['g', 'm']); // critical sorts first
    expect(cards.find((c) => c.id === 'm')?.badge).toBe('money');
    expect(cards.find((c) => c.id === 'g')?.badge).toBe('ship-gate');
  });

  it('surfaces blocked + delivered tasks as founder-action cards', () => {
    const detail: ProjectDetail = {
      ...project({ id: 'px', name: 'Site' }),
      plan: {},
      assigned_agents: ['eng'],
      tasks: [
        { id: 't1', title: 'Connect Stripe', agent: 'eng', status: 'blocked' },
        { id: 't2', title: 'Ship draft', agent: 'eng', status: 'delivered' },
        { id: 't3', title: 'Working', agent: 'eng', status: 'active' },
      ],
    };
    const cards = buildNeedsYou([], [detail]);
    expect(cards).toHaveLength(2);
    const blocked = cards.find((c) => c.id === 'task:t1');
    expect(blocked?.kind).toBe('task');
    expect(blocked?.badge).toBe('decision');
    expect(blocked?.projectName).toBe('Site');
    const delivered = cards.find((c) => c.id === 'task:t2');
    expect(delivered?.badge).toBe('ship-gate');
  });

  it('sorts the merged list by severity desc', () => {
    const detail: ProjectDetail = {
      ...project({ id: 'px' }),
      plan: {},
      assigned_agents: [],
      tasks: [{ id: 't1', title: 'blocked', agent: 'a', status: 'blocked' }],
    };
    const cards = buildNeedsYou(
      [approval({ id: 'crit', severity: 'critical', action_type: 'override' })],
      [detail],
    );
    // critical approval (5) before blocked task (high=4)
    expect(cards[0]?.id).toBe('crit');
    expect(cards[1]?.id).toBe('task:t1');
  });
});

describe('buildBacklog', () => {
  it('keeps only draft projects', () => {
    const out = buildBacklog([
      project({ id: 'd', status: 'draft' }),
      project({ id: 'a', status: 'active' }),
    ]);
    expect(out.map((p) => p.id)).toEqual(['d']);
  });
});

describe('buildInFlight', () => {
  it('computes clamped progress and task counts', () => {
    const detail: ProjectDetail = {
      ...project({ id: 'p1' }),
      plan: {},
      assigned_agents: ['x'],
      tasks: [
        { id: 't1', title: 'a', agent: 'x', status: 'completed' },
        { id: 't2', title: 'b', agent: 'x', status: 'delivered' },
        { id: 't3', title: 'c', agent: 'x', status: 'active' },
      ],
    };
    const map = new Map([['p1', detail]]);
    const [card] = buildInFlight([project({ id: 'p1', funded_amount: 250, target_amount: 1000 })], map);
    expect(card?.progress).toBeCloseTo(0.25);
    expect(card?.taskCounts).toEqual({ total: 3, done: 2 });
  });

  it('returns null progress when target is missing/zero', () => {
    const [card] = buildInFlight(
      [project({ id: 'p1', target_amount: null })],
      new Map(),
    );
    expect(card?.progress).toBeNull();
  });
});

describe('buildDone', () => {
  it('joins completed/cancelled projects with episode summaries and flags cancelled', () => {
    const episodes: EpisodeRow[] = [
      {
        project_id: 'c1',
        summary: 'shipped it',
        retention_tier: 'summary',
        run_id: null,
        created_at: '',
        updated_at: '',
      },
    ];
    const out = buildDone(
      [
        project({ id: 'c1', status: 'completed' }),
        project({ id: 'x1', status: 'cancelled' }),
        project({ id: 'a1', status: 'active' }),
      ],
      episodes,
    );
    expect(out.map((d) => d.project.id)).toEqual(['c1', 'x1']);
    expect(out.find((d) => d.project.id === 'c1')?.summary).toBe('shipped it');
    expect(out.find((d) => d.project.id === 'x1')?.cancelled).toBe(true);
    expect(out.find((d) => d.project.id === 'x1')?.summary).toBeNull();
  });
});
