// Model source card — where the company's AI work runs and how it's
// billed. A custom API key books real per-token cost; a subscription
// books its monthly fee as the real recurring expense.

import { useCallback, useEffect, useState } from 'react';
import {
  detectAgentClis,
  getModelSource,
  setModelSource,
  type DetectedCli,
  type ModelSource,
} from '../../api/client';
import { useAsync } from '../useAsync';

const sourceLoader = (signal?: AbortSignal) => getModelSource(signal);
const clisLoader = (signal?: AbortSignal) => detectAgentClis(signal);

// Founder-facing description of the derived execution loop per kind —
// mirrors the legacy page's MS_SUMMARIES. Never says "runner"; the
// engine picks the loop from the source kind.
const SUMMARIES: Record<string, string> = {
  claude_subscription:
    'Tasks execute via the Claude Code CLI on your Claude subscription. Per-call spend is shadow value; the monthly fee is the real recurring expense.',
  openai_subscription:
    'Tasks execute via the Codex CLI on your OpenAI subscription. Per-call spend is shadow value; the monthly fee is the real recurring expense.',
  custom_api:
    'Tasks execute via the opencode CLI with your API key. Every call books real per-token cost to the ledger.',
  '': 'No model source — every call books real per-token cost via your API key (legacy mode).',
};

function clisSummary(clis: Record<string, DetectedCli> | null): string {
  if (!clis) return '';
  const parts = Object.entries(clis).map(
    ([name, info]) => `${name} CLI ${info.found ? '✓' : '✗'}${info.version ? ' ' + info.version : ''}`,
  );
  return parts.length ? 'detected: ' + parts.join(' · ') : '';
}

export function ModelSourceCard() {
  const source = useAsync<ModelSource | null>(sourceLoader);
  const clis = useAsync<Record<string, DetectedCli>>(clisLoader);

  const [kind, setKind] = useState('');
  const [fee, setFee] = useState('');
  const [confirmClear, setConfirmClear] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  useEffect(() => {
    if (source.state === 'ready') {
      setKind(source.data?.kind ?? '');
      setFee(
        source.data?.monthly_fee_usd != null ? String(source.data.monthly_fee_usd) : '',
      );
    }
  }, [source.data, source.state]);

  const showFee = kind === 'claude_subscription' || kind === 'openai_subscription';

  const onKindChange = useCallback((next: string) => {
    setKind(next);
    setConfirmClear(false);
  }, []);

  const onSave = useCallback(async () => {
    if (kind) {
      const body: { kind: string; monthly_fee_usd?: number } = { kind };
      if (showFee) {
        const parsed = parseFloat(fee);
        if (Number.isNaN(parsed)) {
          setResult({ ok: false, detail: 'monthly fee required for a subscription source' });
          return;
        }
        body.monthly_fee_usd = parsed;
      }
      setSaving(true);
      setResult(null);
      try {
        await setModelSource(body);
        setResult({ ok: true, detail: `model source set to ${kind}` });
        source.reload();
      } catch (err) {
        setResult({
          ok: false,
          detail: err instanceof Error ? err.message : 'save failed',
        });
      } finally {
        setSaving(false);
      }
      return;
    }
    // Clearing back to legacy per-token billing needs a second click.
    if (!confirmClear) {
      setConfirmClear(true);
      setResult({ ok: true, detail: 'click again to confirm removing the model source' });
      return;
    }
    setSaving(true);
    setResult(null);
    try {
      await setModelSource({ kind: null });
      setResult({ ok: true, detail: 'model source cleared' });
      source.reload();
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'save failed',
      });
    } finally {
      setSaving(false);
      setConfirmClear(false);
    }
  }, [kind, fee, showFee, confirmClear, source]);

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Model Source</h2>
      </header>
      <p className="settings__hint">
        Where the company's AI work runs and how it's billed. A custom API
        key books real per-token cost; a subscription books its monthly fee
        as the real recurring expense (per-call spend is tracked as shadow
        value only).
      </p>

      {clis.state === 'ready' && (
        <p className="settings__meta">{clisSummary(clis.data) || 'CLI probe returned nothing'}</p>
      )}

      <label className="settings__field">
        <span className="settings__label">Source</span>
        <select
          className="settings__select"
          value={kind}
          onChange={(e) => onKindChange(e.target.value)}
        >
          <option value="">(not configured — per-token API billing)</option>
          <option value="custom_api">Custom API key</option>
          <option value="claude_subscription">Claude subscription</option>
          <option value="openai_subscription">OpenAI subscription</option>
        </select>
      </label>

      {showFee && (
        <label className="settings__field">
          <span className="settings__label">Monthly fee (USD)</span>
          <input
            className="settings__input"
            type="number"
            min={0}
            step="0.01"
            placeholder="20.00"
            value={fee}
            onChange={(e) => setFee(e.target.value)}
          />
        </label>
      )}

      <p className="settings__meta">{SUMMARIES[kind] ?? ''}</p>

      <div className="settings__actions">
        <button className="btn btn--primary btn--sm" onClick={onSave} disabled={saving}>
          {saving
            ? 'Saving…'
            : !kind && confirmClear
              ? 'Confirm — back to per-token billing'
              : 'Save'}
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
