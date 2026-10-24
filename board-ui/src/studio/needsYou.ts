// NEEDS YOU — the founder's three jobs, and nothing else (09-08-studio-ui
// PR2). Every item in this list is one of:
//   money    — approve spend (envelope top-up, budget, paid tool action)
//   account  — connect an integration the team is blocked on
//   decision — everything else the company cannot decide alone
// Founder labor never appears: a `delivered` task waiting for a human to
// "finish it" is the old YOUR-MOVE stopgap and is deliberately dropped.
// Pure functions — no fetching or React — so the matrix is unit-testable.

import type { IntegrationInfo } from '../api/client';
import type { ApprovalRequest, ProjectDetail, ProjectTask } from '../api/types';
import { MONEY_ACTIONS, severityRank } from '../board/classify';

export type NeedsKind = 'money' | 'account' | 'decision';

export interface NeedsItem {
  id: string;
  kind: NeedsKind;
  title: string;
  severity: string;
  /** Who raised it (agent role) when known. */
  agent?: string;
  projectId?: string;
  projectName?: string;
  /** kind==='money' | 'decision' from an approval row: the row to act on. */
  approval?: ApprovalRequest;
  /** kind==='account': the integration to connect (one click → Settings). */
  integration?: Pick<IntegrationInfo, 'integration_id' | 'display_name'>;
  /** kind==='decision' from a blocked task: text prefilled for the CEO. */
  escalation?: string;
  /** Blocked task: the task to requeue with "Retry". */
  taskId?: string;
  /** Why it is here — block_reason from the watchdog or the runner's founder_action. */
  reason?: string;
  /** The team already retried and gave up (`retry_exhausted`): not a product call. */
  stuck?: boolean;
  /** Plain-language reading of `reason`; the raw text stays available as detail. */
  summary?: string;
}

/**
 * Turn an engine block reason into one sentence a founder can act on.
 * `retry_exhausted: run failed 3 times (error_max_turns: turn cap 30 reached
 * without done=true; linkedin_feed failed 76 times: Cannot find module 'x')`
 * becomes "linkedin_feed kept failing: a required module (x) is not
 * installed on the server. The team tried 3 times." Unknown shapes return
 * undefined so the card falls back to the raw reason.
 */
export function explainBlock(reason: string | undefined): string | undefined {
  if (!reason) return undefined;
  const tries = /run (?:failed|died) (\d+) times/.exec(reason)?.[1];
  const tried = tries ? ` The team tried ${tries} times.` : '';
  const tool = /([\w.-]+) failed \d+ times: ([^)]+)/.exec(reason);
  if (tool) {
    const missing = /Cannot find module '([^']+)'|No module named '([^']+)'/.exec(tool[2]!);
    const what = missing
      ? `a required module (${missing[1] ?? missing[2]}) is not installed on the server`
      : tool[2]!.trim();
    return `Tool ${tool[1]} kept failing: ${what}.${tried}`;
  }
  const cap = /turn cap (\d+) reached/.exec(reason);
  if (cap) return `The agent used all ${cap[1]} steps without finishing.${tried}`;
  if (reason.startsWith('retry_exhausted')) return `The run kept failing.${tried}`;
  return undefined;
}

export const KIND_LABEL: Record<NeedsKind, string> = {
  money: 'money',
  account: 'connect',
  decision: 'decision',
};

export const KIND_ORDER: NeedsKind[] = ['money', 'account', 'decision'];

const PENDING: ReadonlySet<string> = new Set(['pending', 'snoozed']);

/** Approval rows: money is the explicit allowlist; every other gate is a decision. */
export function kindForApproval(a: ApprovalRequest): NeedsKind {
  return MONEY_ACTIONS.has(a.action_type) ? 'money' : 'decision';
}

function norm(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

/**
 * A blocked task names the integration it waits on ("blocked: connect Stripe",
 * "needs Telegram credentials"). Match against the registry; only a NOT yet
 * connected integration counts — a connected one means the block is something
 * else and belongs to the CEO.
 */
export function integrationForTask(
  task: ProjectTask,
  integrations: IntegrationInfo[],
): IntegrationInfo | null {
  const words = ` ${norm(task.title)} `;
  for (const i of integrations) {
    if (i.connected) continue;
    const names = [i.display_name, i.integration_id].map(norm).filter(Boolean);
    if (names.some((n) => words.includes(` ${n} `))) return i;
  }
  return null;
}

/**
 * Build the list: pending/snoozed approvals + blocked tasks of active
 * projects. Blocked task with a connectable integration → account card;
 * other blocked tasks → CEO escalation (decision). Delivered tasks are
 * founder labor and are never emitted. Sorted money → account → decision,
 * then by severity.
 */
export function buildNeedsYou(
  inbox: ApprovalRequest[],
  projects: ProjectDetail[],
  integrations: IntegrationInfo[],
): NeedsItem[] {
  const items: NeedsItem[] = [];
  for (const a of inbox) {
    if (!PENDING.has(a.status)) continue;
    items.push({
      id: a.id,
      kind: kindForApproval(a),
      title: a.summary,
      severity: a.severity,
      agent: a.requested_by ?? undefined,
      projectId: a.project_id ?? undefined,
      approval: a,
    });
  }
  for (const p of projects) {
    for (const t of p.tasks) {
      if (t.status !== 'blocked') continue;
      const integ = integrationForTask(t, integrations);
      if (integ) {
        items.push({
          id: `task:${t.id}`,
          kind: 'account',
          title: `connect ${integ.display_name}`,
          severity: 'high',
          agent: t.agent,
          projectId: p.id,
          projectName: p.name,
          integration: { integration_id: integ.integration_id, display_name: integ.display_name },
        });
      } else {
        const reason = t.block_reason || t.result?.founder_action || undefined;
        const summary = explainBlock(reason);
        items.push({
          id: `task:${t.id}`,
          kind: 'decision',
          title: t.title,
          severity: 'high',
          agent: t.agent,
          projectId: p.id,
          projectName: p.name,
          taskId: t.id,
          reason,
          summary,
          stuck: (t.block_reason ?? '').startsWith('retry_exhausted'),
          escalation:
            `Task "${t.title}" (${t.agent}, project ${p.name}) is blocked` +
            (summary ? ` — ${summary}` : '') +
            (reason ? ` Engine detail: ${reason}` : '') +
            `. What should the team do?`,
        });
      }
    }
  }
  return items.sort(
    (x, y) =>
      KIND_ORDER.indexOf(x.kind) - KIND_ORDER.indexOf(y.kind) ||
      severityRank(y.severity) - severityRank(x.severity),
  );
}

/** Counts per kind for the rail header / nav badge. */
export function countByKind(items: NeedsItem[]): Record<NeedsKind, number> {
  const c: Record<NeedsKind, number> = { money: 0, account: 0, decision: 0 };
  for (const i of items) c[i.kind] += 1;
  return c;
}
