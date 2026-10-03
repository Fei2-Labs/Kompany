import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ApprovalRequest } from '../src/api/types';
import {
  LONG_VALUE_CHARS,
  formatScalar,
  humanizeKey,
  previewOf,
  summarizeApprovalPayload,
  type SummarySection,
} from '../src/studio/approvalSummary';
import { ApprovalSummary } from '../src/studio/PayloadSummary';

// Shapes mirror the backend producers:
//   target_feasibility — kompany/core/target_review/_orchestration.py
//   csuite_review      — kompany/core/csuite_review.py

const feasibility = {
  cfo_view: 'Runway covers 4 months at current burn.',
  cos_view: 'Two launches in parallel overload the team.',
  ceo_proposal: 'Cut the revenue target to $2,189 and keep the deadline.',
  cfo_claims: [
    {
      text: 'Burn is $1,200 per month.',
      evidence: [{ source_type: 'ledger_entry', source_ref: 'le_1', claim_supported: 'burn' }],
    },
    { text: 'Market will grow.', evidence: [{ source_type: 'inferred', source_ref: '' }] },
    { text: 'No evidence at all.', evidence: [] },
  ],
  cos_claims: [],
  ceo_claims: [{ text: 'Founder asked for $10k.', evidence: [{ source_type: 'user_input' }] }],
  rationale: 'The ledger does not support the original number.',
  original_targets: { revenue_target: 10000, customer_target: 50, initial_budget: 5000, deadline: '2026-12-31T00:00:00+00:00' },
  recommended_targets: { revenue_target: 2189, customer_target: 50, initial_budget: 5000, deadline: '2026-12-31T00:00:00+00:00' },
  rounds: [{ generation: 1 }],
  generation: 1,
  ceo_only: false,
  revision_hint: null,
};

const csuite = {
  task_id: 't_123',
  project_id: 'p_9',
  deliverable_class: 'outward_post',
  findings: [
    { role: 'cmo', verdict: 'HOLD', defects: ['Claims a customer count we cannot verify.', 'Tone is too salesy.'] },
    { role: 'ciso', verdict: 'PASS', defects: [] },
  ],
  review_failed_closed: false,
  reason: 'At least one reviewer held.',
};

const titles = (s: SummarySection[]) => s.map((x) => x.title);

describe('humanizeKey / formatScalar', () => {
  it('humanizes snake_case keys with acronyms', () => {
    expect(humanizeKey('cfo_claims')).toBe('CFO claims');
    expect(humanizeKey('deliverable_class')).toBe('Deliverable class');
    expect(humanizeKey('task_id')).toBe('Task ID');
    expect(humanizeKey('cos_view')).toBe('CoS view');
  });

  it('formats scalars and drops empty values', () => {
    expect(formatScalar(true)).toBe('Yes');
    expect(formatScalar(12345)).toBe('12,345');
    expect(formatScalar('  ')).toBeNull();
    expect(formatScalar(null)).toBeNull();
    expect(formatScalar({})).toBeNull();
  });
});

describe('target_feasibility', () => {
  const s = summarizeApprovalPayload('target_feasibility', feasibility);

  it('leads with the CEO proposal and shows views, rationale and claims', () => {
    expect(s[0]!).toEqual({ title: 'CEO proposal', text: feasibility.ceo_proposal });
    expect(titles(s)).toEqual([
      'CEO proposal',
      'Targets (yours → recommended)',
      'Team views',
      'Rationale',
      'CEO claims',
      'CFO claims',
    ]);
    const views = s.find((x) => x.title === 'Team views')!;
    expect(views.rows!.map((r) => r.label)).toEqual(['CFO', 'CoS']);
  });

  it('shows only changed targets as from → to', () => {
    const rows = s.find((x) => x.title?.startsWith('Targets'))!.rows!;
    expect(rows.find((r) => r.label === 'Revenue')!.value).toBe('$10,000 → $2,189');
    expect(rows.find((r) => r.label === 'Customers')!.value).toBe('50');
    expect(rows.find((r) => r.label === 'Deadline')!.value).toBe('2026-12-31');
  });

  it('labels claims by evidence and flags inferred-only ones', () => {
    const items = s.find((x) => x.title === 'CFO claims')!.items!;
    expect(items[0]).toMatchObject({ text: 'Burn is $1,200 per month.', badge: 'ledger entry', tone: 'muted' });
    expect(items[1]).toMatchObject({ badge: 'inferred only', tone: 'warn' });
    expect(items[2]).toMatchObject({ badge: 'inferred only', tone: 'warn' });
  });

  it('omits internal fields (rounds, generation, ceo_only)', () => {
    const text = JSON.stringify(s);
    expect(text).not.toContain('generation');
    expect(text).not.toContain('Ceo only');
  });
});

describe('csuite_review', () => {
  const s = summarizeApprovalPayload('csuite_review', csuite);

  it('renders one row per finding with verdict and defects', () => {
    const findings = s.find((x) => x.title === 'Findings')!.items!;
    expect(findings).toEqual([
      { text: 'CMO', badge: 'HOLD', tone: 'hold', details: csuite.findings[0]!.defects },
      { text: 'CISO', badge: 'PASS', tone: 'ok', details: undefined },
    ]);
  });

  it('shows deliverable and reason, not internal ids', () => {
    expect(s[0]!.rows).toEqual([
      { label: 'Deliverable', value: 'Outward post', depth: 0 },
      { label: 'Reason', value: 'At least one reviewer held.', depth: 0 },
    ]);
    expect(JSON.stringify(s)).not.toContain('t_123');
  });

  it('warns when the review failed closed', () => {
    const f = summarizeApprovalPayload('csuite_review', { ...csuite, review_failed_closed: true });
    expect(f.find((x) => x.title === 'Findings')!.items![0]!).toMatchObject({ badge: 'failed closed', tone: 'warn' });
  });
});

describe('unknown or drifted shapes', () => {
  it('renders humanized key/value rows, nested and indented', () => {
    const s = summarizeApprovalPayload('budget_increase', {
      amount_usd: 250,
      reason: 'Ads test',
      effect_applied: true,
      breakdown: { ads: 200, tools: ['figma', 'canva'] },
      steps: [{ name: 'a' }, 'b'],
    });
    expect(s).toHaveLength(1);
    expect(s[0]!.rows).toEqual([
      { label: 'Amount USD', value: '250', depth: 0 },
      { label: 'Reason', value: 'Ads test', depth: 0 },
      { label: 'Breakdown', value: '', depth: 0 },
      { label: 'Ads', value: '200', depth: 1 },
      { label: 'Tools', value: 'figma, canva', depth: 1 },
      { label: 'Steps', value: '', depth: 0 },
      { label: '1.', value: '', depth: 1 },
      { label: 'Name', value: 'a', depth: 2 },
      { label: '2.', value: 'b', depth: 1 },
    ]);
  });

  it('caps nesting depth instead of recursing forever', () => {
    const deep = { a: { b: { c: { d: { e: 1 } } } } };
    const rows = summarizeApprovalPayload('x', deep)[0]!.rows!;
    expect(rows.at(-1)).toEqual({ label: 'D', value: '1 fields', depth: 3 });
  });

  it('falls back when a known type has drifted fields', () => {
    const s = summarizeApprovalPayload('csuite_review', { verdicts: 'something new' });
    expect(s[0]!.rows).toEqual([{ label: 'Verdicts', value: 'something new', depth: 0 }]);
  });

  it('never throws on junk', () => {
    for (const p of [null, undefined, 'str', 3, [], { findings: 'x' }, { cfo_claims: [null, 1, { evidence: 'x' }] }]) {
      expect(() => summarizeApprovalPayload('csuite_review', p)).not.toThrow();
      expect(() => summarizeApprovalPayload('target_feasibility', p)).not.toThrow();
      expect(() => summarizeApprovalPayload('other', p)).not.toThrow();
    }
    expect(summarizeApprovalPayload('x', null)).toEqual([]);
  });
});

describe('<ApprovalSummary>', () => {
  const approval = (action_type: string, payload: Record<string, unknown>) =>
    ({ id: 'a1', action_type, payload, summary: '', status: 'pending' }) as unknown as ApprovalRequest;

  it('shows readable text by default and keeps raw JSON collapsed', () => {
    const html = renderToStaticMarkup(createElement(ApprovalSummary, { approval: approval('csuite_review', csuite) }));
    expect(html).toContain('Findings');
    expect(html).toContain('Tone is too salesy.');
    expect(html).toMatch(/<details class="ny-summary__raw"><summary>Raw payload<\/summary>/);
    expect(html).not.toContain('<details class="ny-summary__raw" open');
  });

  it('renders nothing for an empty payload', () => {
    expect(renderToStaticMarkup(createElement(ApprovalSummary, { approval: approval('x', {}) }))).toBe('');
  });
});


describe('long values', () => {
  // A real self_update_proposal carried a 13k-character `instruction`;
  // rendered in full it made one card 4,700px tall.
  const long = `Fix the verified false negative in the gate.\n${'x'.repeat(13000)}`;

  it('flags an oversized generic value and keeps it whole', () => {
    const rows = summarizeApprovalPayload('self_update_proposal', {
      branch: 'auto/fix-1',
      instruction: long,
    })[0]!.rows!;
    expect(rows[0]).toEqual({ label: 'Branch', value: 'auto/fix-1', depth: 0 });
    expect(rows[1]).toMatchObject({ label: 'Instruction', long: true });
    expect(rows[1]!.value).toBe(long);
  });

  it('flags long section text and long known-shape rows', () => {
    const s = summarizeApprovalPayload('target_feasibility', { ceo_proposal: long, cfo_view: long });
    expect(s.find((x) => x.title === 'CEO proposal')).toMatchObject({ long: true });
    expect(s.find((x) => x.title === 'Team views')!.rows![0]).toMatchObject({ long: true });
  });

  it('leaves values at or under the threshold alone', () => {
    const rows = summarizeApprovalPayload('x', { note: 'y'.repeat(LONG_VALUE_CHARS) })[0]!.rows!;
    expect(rows[0]!.long).toBeUndefined();
  });

  it('previews the first line and marks the cut', () => {
    expect(previewOf('First line.\nSecond line.')).toBe('First line. …');
    expect(previewOf('z'.repeat(500))).toBe(`${'z'.repeat(160)} …`);
    expect(previewOf('short')).toBe('short');
  });

  it('renders a long value collapsed, with the full text present', () => {
    const approval = {
      id: 'a', action_type: 'self_update_proposal', payload: { instruction: long }, summary: '', status: 'pending',
    } as unknown as ApprovalRequest;
    const html = renderToStaticMarkup(createElement(ApprovalSummary, { approval }));
    expect(html).toContain('<details class="ny-summary__more"><summary>Fix the verified false negative in the gate. …</summary>');
    expect(html).not.toContain('ny-summary__more" open');
    expect(html).toContain('x'.repeat(13000));
  });
});

describe('reviewer role labels', () => {
  const roles = (findings: unknown) =>
    summarizeApprovalPayload('csuite_review', { findings })
      .find((x) => x.title === 'Findings')!
      .items!.map((i) => i.text);

  it('uppercases C-suite acronyms, including ones not in the map', () => {
    // A live payload carried `cpo`, which used to render as "Cpo".
    expect(roles([{ role: 'cpo' }, { role: 'cto' }, { role: 'cos' }])).toEqual(['CPO', 'CTO', 'CoS']);
  });

  it('humanizes longer role names and survives a missing role', () => {
    expect(roles([{ role: 'head_of_growth' }, {}])).toEqual(['Head of growth', 'Reviewer']);
  });
});
