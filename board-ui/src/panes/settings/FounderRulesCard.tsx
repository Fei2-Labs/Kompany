// Founder rules card — hard rules are enforced (excluded capabilities
// filtered out of every plan + blocked at execution; budget caps bound any
// single action; forbidden paid categories are refused). Soft preferences
// are injected into agent prompts best-effort.

import { useCallback, useEffect, useState } from 'react';
import {
  getFounderRules,
  setFounderRules,
  type FounderHardRule,
  type FounderRuleKind,
} from '../../api/client';
import { useAsync } from '../useAsync';

const RULE_KINDS: Array<{ value: FounderRuleKind; label: string }> = [
  { value: 'exclude_capability', label: 'exclude capability' },
  { value: 'budget_cap', label: 'budget cap (USD/action)' },
  { value: 'forbid_paid_category', label: 'forbid paid category' },
];

let nextRowId = 0;
interface RuleRow extends FounderHardRule {
  _id: number;
}

const rulesLoader = (signal?: AbortSignal) => getFounderRules(signal);

export function FounderRulesCard() {
  const rules = useAsync(rulesLoader);
  const [soft, setSoft] = useState('');
  const [rows, setRows] = useState<RuleRow[]>([]);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  useEffect(() => {
    if (rules.state === 'ready') {
      setSoft(rules.data?.soft ?? '');
      setRows(
        (rules.data?.hard ?? []).map((r) => ({ ...r, _id: nextRowId++ })),
      );
    }
  }, [rules.data, rules.state]);

  const addRow = useCallback(() => {
    setRows((r) => [
      ...r,
      { _id: nextRowId++, kind: 'exclude_capability', match: '', action: 'skip' },
    ]);
  }, []);

  const removeRow = useCallback((id: number) => {
    setRows((r) => r.filter((row) => row._id !== id));
  }, []);

  const updateRow = useCallback(
    (id: number, patch: Partial<FounderHardRule>) => {
      setRows((r) => r.map((row) => (row._id === id ? { ...row, ...patch } : row)));
    },
    [],
  );

  const onSave = useCallback(async () => {
    const hard: FounderHardRule[] = rows
      .filter((r) => r.match.trim())
      .map((r) => ({ kind: r.kind, match: r.match.trim(), action: 'skip' }));
    const softTrimmed = soft.trim();
    setSaving(true);
    setResult(null);
    try {
      if (!hard.length && !softTrimmed) {
        await setFounderRules({ clear: true });
        setResult({ ok: true, detail: 'rules cleared' });
      } else {
        await setFounderRules({ hard, soft: softTrimmed });
        setResult({ ok: true, detail: `rules saved (${hard.length} hard)` });
      }
      rules.reload();
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'save failed',
      });
    } finally {
      setSaving(false);
    }
  }, [rows, soft, rules]);

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Founder Rules</h2>
      </header>
      <p className="settings__hint">
        Hard rules are enforced — excluded capabilities are filtered out of
        every plan and blocked at execution; budget caps bound any single
        action; forbidden paid categories are refused. Soft preferences are
        injected into agent prompts (best-effort).
      </p>

      <label className="settings__field">
        <span className="settings__label">Soft preferences</span>
        <textarea
          className="settings__textarea"
          rows={3}
          value={soft}
          placeholder="prefer async over meetings; weekly summary on Fridays"
          onChange={(e) => setSoft(e.target.value)}
        />
      </label>

      <p className="settings__meta">
        Hard rules — match is a keyword (exclude / paid category) or a
        per-action USD cap (budget cap).
      </p>

      <div>
        {rows.map((row) => (
          <div className="settings__rule-row" key={row._id}>
            <select
              className="settings__select settings__rule-kind"
              value={row.kind}
              onChange={(e) =>
                updateRow(row._id, { kind: e.target.value as FounderRuleKind })
              }
            >
              {RULE_KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </select>
            <input
              className="settings__input settings__rule-match"
              value={row.match}
              placeholder="phone_call / 10 / ads"
              onChange={(e) => updateRow(row._id, { match: e.target.value })}
            />
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => removeRow(row._id)}
            >
              ×
            </button>
          </div>
        ))}
      </div>

      <div className="settings__actions">
        <button type="button" className="btn btn--ghost btn--sm" onClick={addRow}>
          + add hard rule
        </button>
        <button className="btn btn--primary btn--sm" onClick={onSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save rules'}
        </button>
        {result && (
          <span
            className={
              'settings__result ' +
              (result.ok ? 'settings__result--ok' : 'settings__result--err')
            }
          >
            {result.ok ? '✓ ' : '✗ '}
            {result.detail}
          </span>
        )}
      </div>
    </section>
  );
}
