// Start page card — which screen the desktop app opens after the engine is
// healthy. Stored server-side as the `start_page` UI preference (GET/PATCH
// /preferences); the Tauri shell reads GET /start at launch. Takes effect on
// the next app launch — this card never navigates the current window.

import { useCallback, useEffect, useState } from 'react';
import { useAsync } from '../useAsync';

interface StartOption {
  id: string;
  label: string;
  path: string;
}

interface StartInfo {
  start_page: string;
  path: string;
  board_available: boolean;
  options: StartOption[];
}

const startLoader = async (signal?: AbortSignal): Promise<StartInfo> => {
  const res = await fetch('/start', { signal, headers: { accept: 'application/json' } });
  if (!res.ok) throw new Error(`GET /start → ${res.status}`);
  return (await res.json()) as StartInfo;
};

export function StartPageCard() {
  const info = useAsync<StartInfo>(startLoader);
  const [choice, setChoice] = useState('');
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  useEffect(() => {
    if (info.state === 'ready' && info.data) setChoice(info.data.start_page);
  }, [info.data, info.state]);

  const onSave = useCallback(async () => {
    if (!choice) return;
    setSaving(true);
    setResult(null);
    try {
      const res = await fetch('/preferences', {
        method: 'PATCH',
        headers: { accept: 'application/json', 'content-type': 'application/json' },
        body: JSON.stringify({ start_page: choice }),
      });
      if (!res.ok) throw new Error(`PATCH /preferences → ${res.status}`);
      setResult({ ok: true, detail: 'saved — opens on the next launch' });
      info.reload();
    } catch (err) {
      setResult({ ok: false, detail: err instanceof Error ? err.message : 'save failed' });
    } finally {
      setSaving(false);
    }
  }, [choice, info]);

  const options = info.data?.options ?? [];
  const current = options.find((o) => o.id === info.data?.start_page);

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Start page</h2>
      </header>
      <p className="settings__hint">
        What Kompany opens when you launch the desktop app. Takes effect on the
        next launch — this window stays where it is.
        {info.data && !info.data.board_available
          ? ' The board bundle is not built on this engine, so board pages fall back to the terminal.'
          : ''}
      </p>

      {info.state === 'loading' ? (
        <p className="settings__meta">loading…</p>
      ) : info.state === 'error' ? (
        <p className="settings__meta">{info.error ?? 'failed to load'}</p>
      ) : (
        <>
          <label className="settings__field">
            <span className="settings__label">Open at launch</span>
            <select
              className="settings__select"
              value={choice}
              onChange={(e) => setChoice(e.target.value)}
              aria-label="Start page"
            >
              {options.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label} — {o.path}
                </option>
              ))}
            </select>
          </label>

          <p className="settings__meta">currently: {current ? current.label : info.data?.start_page}</p>

          <div className="settings__actions">
            <button className="btn btn--primary btn--sm" onClick={onSave} disabled={saving || !choice}>
              {saving ? 'Saving…' : 'Save'}
            </button>
            {result && (
              <span
                className={
                  'settings__result ' + (result.ok ? 'settings__result--ok' : 'settings__result--err')
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
