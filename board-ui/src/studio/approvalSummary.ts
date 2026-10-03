// Readable summary of an approval payload for NEEDS YOU decision cards.
//
// Pure: no fetch, no React. NeedsYouCard renders the sections; the raw
// JSON stays reachable behind a collapsed control. Payload schemas live
// on the backend and drift, so every field is treated as optional and
// nothing here may throw on an unexpected shape.

export type Tone = 'ok' | 'hold' | 'warn' | 'muted';

export interface SummaryRow {
  label: string;
  value: string;
  /** Nesting level for the generic fallback; 0 for top-level facts. */
  depth: number;
}

export interface SummaryItem {
  text: string;
  badge?: string;
  tone?: Tone;
  details?: string[];
}

export interface SummarySection {
  title?: string;
  text?: string;
  rows?: SummaryRow[];
  items?: SummaryItem[];
}

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === 'string' ? v.trim() : '');
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

// Bookkeeping the backend stamps on payloads; never founder-facing.
const HIDDEN_KEYS = new Set(['effect_applied']);

const ACRONYMS: Record<string, string> = {
  ceo: 'CEO',
  cfo: 'CFO',
  cmo: 'CMO',
  cto: 'CTO',
  cos: 'CoS',
  ciso: 'CISO',
  id: 'ID',
  ids: 'IDs',
  url: 'URL',
  usd: 'USD',
  eur: 'EUR',
  llm: 'LLM',
  api: 'API',
};

/** `cfo_claims` -> "CFO claims", `deliverable_class` -> "Deliverable class". */
export function humanizeKey(key: string): string {
  const words = key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((w) => ACRONYMS[w.toLowerCase()] ?? w.toLowerCase());
  const [first, ...rest] = words;
  if (first === undefined) return key;
  const head = first === first.toLowerCase() ? first.charAt(0).toUpperCase() + first.slice(1) : first;
  return [head, ...rest].join(' ');
}

/** Scalar to display text; null for values that should not render. */
export function formatScalar(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'number') return Number.isFinite(v) ? v.toLocaleString('en-US') : String(v);
  if (typeof v === 'string') return v.trim() === '' ? null : v.trim();
  return null;
}

const MAX_DEPTH = 3;

function genericRows(value: unknown, label: string, depth: number, out: SummaryRow[]): void {
  const scalar = formatScalar(value);
  if (scalar !== null) {
    out.push({ label, value: scalar, depth });
    return;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return;
    const scalars = value.map(formatScalar);
    if (scalars.every((s) => s !== null)) {
      out.push({ label, value: (scalars as string[]).join(', '), depth });
      return;
    }
    if (depth >= MAX_DEPTH) {
      out.push({ label, value: `${value.length} items`, depth });
      return;
    }
    out.push({ label, value: '', depth });
    value.forEach((v, i) => genericRows(v, `${i + 1}.`, depth + 1, out));
    return;
  }
  if (isObj(value)) {
    const keys = Object.keys(value).filter((k) => !HIDDEN_KEYS.has(k));
    if (keys.length === 0) return;
    if (depth >= MAX_DEPTH) {
      out.push({ label, value: `${keys.length} fields`, depth });
      return;
    }
    out.push({ label, value: '', depth });
    for (const k of keys) genericRows(value[k], humanizeKey(k), depth + 1, out);
  }
}

/** Humanized key/value rows for any payload shape. Never throws. */
export function genericSections(payload: Obj): SummarySection[] {
  const rows: SummaryRow[] = [];
  for (const [k, v] of Object.entries(payload)) {
    if (HIDDEN_KEYS.has(k)) continue;
    genericRows(v, humanizeKey(k), 0, rows);
  }
  return rows.length ? [{ rows }] : [];
}

// --- target_feasibility -----------------------------------------------------

function claimItems(claims: unknown): SummaryItem[] {
  const items: SummaryItem[] = [];
  for (const c of arr(claims)) {
    if (!isObj(c)) continue;
    const text = str(c.text);
    if (!text) continue;
    const types = arr(c.evidence)
      .map((e) => (isObj(e) ? str(e.source_type) : ''))
      .filter(Boolean);
    const sourced = types.some((t) => t !== 'inferred');
    const distinct = [...new Set(types.filter((t) => t !== 'inferred').map((t) => t.replace(/_/g, ' ')))];
    items.push(
      sourced
        ? { text, badge: distinct.join(', '), tone: 'muted' }
        : { text, badge: 'inferred only', tone: 'warn' },
    );
  }
  return items;
}

const TARGET_FIELDS: [string, string][] = [
  ['revenue_target', 'Revenue'],
  ['customer_target', 'Customers'],
  ['initial_budget', 'Budget'],
  ['deadline', 'Deadline'],
];

function targetValue(key: string, v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if ((key === 'revenue_target' || key === 'initial_budget') && typeof v === 'number') {
    return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
  }
  if (key === 'deadline' && typeof v === 'string') return v.slice(0, 10);
  return formatScalar(v) ?? '—';
}

function feasibilitySections(p: Obj): SummarySection[] {
  const sections: SummarySection[] = [];
  const proposal = str(p.ceo_proposal);
  if (proposal) sections.push({ title: 'CEO proposal', text: proposal });

  const original = isObj(p.original_targets) ? p.original_targets : {};
  const recommended = isObj(p.recommended_targets) ? p.recommended_targets : {};
  const targetRows: SummaryRow[] = [];
  for (const [key, label] of TARGET_FIELDS) {
    if (!(key in original) && !(key in recommended)) continue;
    const from = targetValue(key, original[key]);
    const to = targetValue(key, recommended[key]);
    targetRows.push({ label, value: from === to ? from : `${from} → ${to}`, depth: 0 });
  }
  if (targetRows.length) sections.push({ title: 'Targets (yours → recommended)', rows: targetRows });

  const views: SummaryRow[] = [];
  for (const [key, label] of [['cfo_view', 'CFO'], ['cos_view', 'CoS']] as const) {
    const v = str(p[key]);
    if (v) views.push({ label, value: v, depth: 0 });
  }
  if (views.length) sections.push({ title: 'Team views', rows: views });

  const rationale = str(p.rationale);
  if (rationale) sections.push({ title: 'Rationale', text: rationale });

  for (const [key, title] of [
    ['ceo_claims', 'CEO claims'],
    ['cfo_claims', 'CFO claims'],
    ['cos_claims', 'CoS claims'],
  ] as const) {
    const items = claimItems(p[key]);
    if (items.length) sections.push({ title, items });
  }

  const hint = str(p.revision_hint);
  if (hint) sections.push({ title: 'Your counter-proposal', text: hint });
  return sections;
}

// --- csuite_review ----------------------------------------------------------

function csuiteSections(p: Obj): SummarySection[] {
  const sections: SummarySection[] = [];
  const rows: SummaryRow[] = [];
  const cls = str(p.deliverable_class);
  if (cls) rows.push({ label: 'Deliverable', value: humanizeKey(cls), depth: 0 });
  const reason = str(p.reason);
  if (reason) rows.push({ label: 'Reason', value: reason, depth: 0 });
  if (rows.length) sections.push({ rows });

  const items: SummaryItem[] = [];
  if (p.review_failed_closed === true) {
    items.push({
      text: 'A reviewer could not run, so the review held the deliverable to be safe.',
      badge: 'failed closed',
      tone: 'warn',
    });
  }
  for (const f of arr(p.findings)) {
    if (!isObj(f)) continue;
    const role = str(f.role);
    const verdict = str(f.verdict).toUpperCase();
    const defects = arr(f.defects).map(str).filter(Boolean);
    items.push({
      text: role ? (ACRONYMS[role.toLowerCase()] ?? humanizeKey(role)) : 'Reviewer',
      badge: verdict || undefined,
      tone: verdict === 'HOLD' ? 'hold' : verdict === 'PASS' ? 'ok' : 'muted',
      details: defects.length ? defects : undefined,
    });
  }
  if (items.length) sections.push({ title: 'Findings', items });
  return sections;
}

const RENDERERS: Record<string, (p: Obj) => SummarySection[]> = {
  target_feasibility: feasibilitySections,
  csuite_review: csuiteSections,
};

/**
 * Sections to render for an approval payload. Known action types get a
 * tailored layout; anything else, or a known type whose tailored layout
 * comes out empty (schema drift), falls back to humanized key/value rows.
 */
export function summarizeApprovalPayload(actionType: string, payload: unknown): SummarySection[] {
  if (!isObj(payload)) return [];
  const render = RENDERERS[actionType];
  if (render) {
    try {
      const sections = render(payload);
      if (sections.length) return sections;
    } catch {
      // fall through to the generic view
    }
  }
  return genericSections(payload);
}
