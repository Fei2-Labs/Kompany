import { describe, expect, it } from 'vitest';
import type { IntegrationInfo } from '../src/api/client';
import type { ApprovalRequest, ProjectDetail } from '../src/api/types';
import { buildNeedsYou, countByKind, integrationForTask, kindForApproval } from '../src/studio/needsYou';

function approval(over: Partial<ApprovalRequest>): ApprovalRequest {
  return {
    id: 'a1', status: 'pending', action_type: 'debate_decision', summary: 's', payload: {},
    directive_id: null, project_id: null, requested_by: 'cfo', resolved_by: null, resolution_reason: null,
    created_at: '2026-09-10T00:00:00Z', resolved_at: null, severity: 'medium', predecessor_id: null,
    snoozed_until: null, snoozed_by: null, ...over,
  };
}
function project(tasks: ProjectDetail['tasks']): ProjectDetail {
  return {
    id: 'p1', name: 'Launch', type: 'product', status: 'active', target_amount: 0, funded_amount: 0,
    plan: {}, assigned_agents: [], tasks,
  } as unknown as ProjectDetail;
}
const stripe: IntegrationInfo = { integration_id: 'stripe', display_name: 'Stripe', description: '', required_credentials: [], connected: false, tools: [] };
const telegram: IntegrationInfo = { ...stripe, integration_id: 'telegram', display_name: 'Telegram', connected: true };

describe('three-kind classifier', () => {
  it('money is the explicit allowlist; every other gate is a decision', () => {
    for (const t of ['envelope_topup', 'budget_increase', 'tool_action']) expect(kindForApproval(approval({ action_type: t }))).toBe('money');
    for (const t of ['delivery_approval', 'channel_post', 'debate_decision', 'self_update', 'weird_new_kind'])
      expect(kindForApproval(approval({ action_type: t }))).toBe('decision');
  });

  it('blocked task naming an unconnected integration → account; connected or unnamed → CEO escalation', () => {
    const tasks = [
      { id: 't1', title: 'Blocked: connect Stripe to accept payments', agent: 'cfo', status: 'blocked' },
      { id: 't2', title: 'Needs telegram credentials', agent: 'cmo', status: 'blocked' },
      { id: 't3', title: 'Waiting for supplier quote', agent: 'coo', status: 'blocked' },
    ];
    expect(integrationForTask(tasks[0]!, [stripe, telegram])?.integration_id).toBe('stripe');
    expect(integrationForTask(tasks[1]!, [stripe, telegram])).toBeNull(); // already connected
    const items = buildNeedsYou([], [project(tasks)], [stripe, telegram]);
    expect(items.map((i) => [i.kind, i.title])).toEqual([
      ['account', 'connect Stripe'],
      ['decision', 'Needs telegram credentials'],
      ['decision', 'Waiting for supplier quote'],
    ]);
    expect(items[1]!.escalation).toContain('blocked');
  });

  it('blocked task carries its reason and a retry handle', () => {
    const items = buildNeedsYou([], [project([
      { id: 't9', title: 'Publish', agent: 'builder', status: 'blocked', block_reason: 'retry_exhausted: run died 3 times', retry_count: 2 },
    ])], []);
    expect(items[0]!.taskId).toBe('t9');
    expect(items[0]!.reason).toBe('retry_exhausted: run died 3 times');
    expect(items[0]!.escalation).toContain('retry_exhausted');
  });

  it('never renders founder labor: delivered / completed tasks and resolved approvals are dropped', () => {
    const items = buildNeedsYou(
      [approval({ id: 'done', status: 'approved' }), approval({ id: 'snz', status: 'snoozed' })],
      [project([
        { id: 't1', title: 'Publish the landing page', agent: 'cmo', status: 'delivered' },
        { id: 't2', title: 'Write copy', agent: 'cmo', status: 'completed' },
      ])],
      [],
    );
    expect(items.map((i) => i.id)).toEqual(['snz']);
  });

  it('orders money → account → decision, severity within kind', () => {
    const items = buildNeedsYou(
      [
        approval({ id: 'd-low', severity: 'low' }),
        approval({ id: 'm', action_type: 'envelope_topup', severity: 'low' }),
        approval({ id: 'd-crit', severity: 'critical' }),
      ],
      [project([{ id: 't1', title: 'connect stripe', agent: 'cfo', status: 'blocked' }])],
      [stripe],
    );
    expect(items.map((i) => i.id)).toEqual(['m', 'task:t1', 'd-crit', 'd-low']);
    expect(countByKind(items)).toEqual({ money: 1, account: 1, decision: 2 });
  });
});
