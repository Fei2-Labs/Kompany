// Pure board-composition helpers: classify approval cards by action_type,
// rank by severity, and assemble the four columns from the raw REST shapes.
// No fetching or React here so the mapping is unit-testable in isolation
// (tests/classify.test.ts). Sets come straight from the research doc.

import type {
  ApprovalRequest,
  EpisodeRow,
  ProjectDetail,
  ProjectListItem,
  Severity,
} from '../api/types';

/** Needs-You sub-badge for an approval card. */
export type ActionBadge = 'money' | 'ship-gate' | 'decision';

const MONEY_ACTIONS: ReadonlySet<string> = new Set([
  'envelope_topup',
  'budget_increase',
  'tool_action',
]);

const SHIP_GATE_ACTIONS: ReadonlySet<string> = new Set([
  'delivery_approval',
  'channel_post',
  'deai_gate',
  'fabrication_check',
  'outward_park',
  'csuite_review',
]);

/**
 * Classify an approval's `action_type` into a Needs-You badge.
 * money / ship-gate are explicit allowlists (research §3); everything
 * else (debate/target/glossary/override/self_update/…) is a decision.
 */
export function classifyAction(actionType: string): ActionBadge {
  if (MONEY_ACTIONS.has(actionType)) return 'money';
  if (SHIP_GATE_ACTIONS.has(actionType)) return 'ship-gate';
  return 'decision';
}

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 5,
  high: 4,
  medium: 3,
  low: 2,
  info: 1,
};

/** Numeric weight for sorting; higher = more urgent. Unknown → 0. */
export function severityRank(severity: string): number {
  return SEVERITY_RANK[severity as Severity] ?? 0;
}

/** A Needs-You card, unified across approvals and blocked/delivered tasks. */
export interface NeedsYouCard {
  id: string;
  kind: 'approval' | 'task';
  title: string;
  badge: ActionBadge;
  severity: string;
  /** Originating approval (kind==='approval') for the PR3 action seam. */
  approval?: ApprovalRequest;
  /** Originating task (kind==='task') + its project for context. */
  taskStatus?: 'blocked' | 'delivered';
  projectId?: string;
  projectName?: string;
  agent?: string;
}

const APPROVAL_PENDING: ReadonlySet<string> = new Set(['pending', 'snoozed']);

/**
 * Build the Needs-You column: pending/snoozed approvals classified by badge,
 * PLUS blocked/delivered tasks from active projects surfaced as founder
 * actions. Sorted by severity (desc); blocked tasks rank as `high`,
 * delivered as `medium`. Account-connect is a blocked task, not an approval.
 */
export function buildNeedsYou(
  inbox: ApprovalRequest[],
  activeDetails: ProjectDetail[],
): NeedsYouCard[] {
  const cards: NeedsYouCard[] = [];

  for (const a of inbox) {
    if (!APPROVAL_PENDING.has(a.status)) continue;
    cards.push({
      id: a.id,
      kind: 'approval',
      title: a.summary,
      badge: classifyAction(a.action_type),
      severity: a.severity,
      approval: a,
    });
  }

  for (const p of activeDetails) {
    for (const t of p.tasks) {
      if (t.status !== 'blocked' && t.status !== 'delivered') continue;
      cards.push({
        id: `task:${t.id}`,
        kind: 'task',
        title: t.title,
        // blocked = connect-account / unblock (money-adjacent decision);
        // delivered = founder must finish/ship → ship-gate-flavoured.
        badge: t.status === 'blocked' ? 'decision' : 'ship-gate',
        severity: t.status === 'blocked' ? 'high' : 'medium',
        taskStatus: t.status,
        projectId: p.id,
        projectName: p.name,
        agent: t.agent,
      });
    }
  }

  return cards.sort((x, y) => severityRank(y.severity) - severityRank(x.severity));
}

/** Backlog: draft projects. Requires the include_draft list. */
export function buildBacklog(
  withDraft: ProjectListItem[],
): ProjectListItem[] {
  return withDraft.filter((p) => p.status === 'draft');
}

/** An In-Flight card: active project + (optional) enriched task counts. */
export interface InFlightCard {
  project: ProjectListItem;
  detail?: ProjectDetail;
  /** funded_amount / target_amount, clamped to [0,1]; null if no target. */
  progress: number | null;
  taskCounts?: { total: number; done: number };
}

/** Compose In-Flight from active projects, enriching with details when present. */
export function buildInFlight(
  active: ProjectListItem[],
  detailsById: Map<string, ProjectDetail>,
): InFlightCard[] {
  return active.map((project) => {
    const detail = detailsById.get(project.id);
    const target = project.target_amount;
    const progress =
      target && target > 0
        ? Math.max(0, Math.min(1, project.funded_amount / target))
        : null;
    let taskCounts: InFlightCard['taskCounts'];
    if (detail) {
      const done = detail.tasks.filter((t) =>
        ['completed', 'delivered'].includes(t.status),
      ).length;
      taskCounts = { total: detail.tasks.length, done };
    }
    return { project, detail, progress, taskCounts };
  });
}

const DONE_STATUSES: ReadonlySet<string> = new Set(['completed', 'cancelled']);

/** A Done card: finished project joined with its episode summary. */
export interface DoneCard {
  project: ProjectListItem;
  cancelled: boolean;
  summary: string | null;
}

/** Compose Done from completed/cancelled projects joined with episodes. */
export function buildDone(
  withDraft: ProjectListItem[],
  episodes: EpisodeRow[],
): DoneCard[] {
  const summaryByProject = new Map<string, string>();
  for (const e of episodes) summaryByProject.set(e.project_id, e.summary);
  return withDraft
    .filter((p) => DONE_STATUSES.has(p.status))
    .map((project) => ({
      project,
      cancelled: project.status === 'cancelled',
      summary: summaryByProject.get(project.id) ?? null,
    }));
}
