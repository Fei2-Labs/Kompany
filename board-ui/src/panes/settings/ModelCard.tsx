// LLM Model card — switch the model all three tiers (apex/primary/economy)
// use. Applies to the live engine immediately, no restart. Mirrors the
// legacy /ui/settings.html "LLM MODEL" section.

import { useCallback, useEffect, useState } from 'react';
import { getModelSetting, setModelSetting, type ModelSetting } from '../../api/client';
import { useAsync } from '../useAsync';

const modelLoader = (signal?: AbortSignal) => getModelSetting(signal);

export function ModelCard() {
  const model = useAsync<ModelSetting>(modelLoader);
  const [selected, setSelected] = useState('');
  const [applying, setApplying] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  useEffect(() => {
    if (model.state === 'ready' && model.data) {
      setSelected(model.data.current_model || '');
    }
  }, [model.data, model.state]);

  const onApply = useCallback(async () => {
    if (!selected) return;
    setApplying(true);
    setResult(null);
    try {
      const d = await setModelSetting(selected);
      setResult({ ok: true, detail: `switched to ${d.current_model}` });
      model.reload();
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'switch failed',
      });
    } finally {
      setApplying(false);
    }
  }, [selected, model]);

  const options = model.data?.available_models?.length
    ? model.data.available_models
    : model.data?.current_model
      ? [model.data.current_model]
      : [];

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">LLM Model</h2>
      </header>
      <p className="settings__hint">
        All three tiers (apex / primary / economy) use this model. Switching
        takes effect immediately — no restart.
      </p>

      {model.state === 'loading' ? (
        <p className="settings__meta">loading…</p>
      ) : model.state === 'error' ? (
        <p className="settings__meta">{model.error ?? 'failed to load'}</p>
      ) : (
        <>
          <label className="settings__field">
            <span className="settings__label">Active model</span>
            <select
              className="settings__select"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              {!options.length && <option value="">(no models — check provider)</option>}
              {options.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>

          <p className="settings__meta">
            provider: {model.data?.provider || '?'}
            {model.data?.base_url ? ` · ${model.data.base_url}` : ''}
            {model.data?.error ? ` · ⚠ ${model.data.error}` : ''}
          </p>

          <div className="settings__actions">
            <button
              className="btn btn--primary btn--sm"
              onClick={onApply}
              disabled={applying || !selected}
            >
              {applying ? 'Applying…' : 'Apply'}
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
        </>
      )}
    </section>
  );
}
