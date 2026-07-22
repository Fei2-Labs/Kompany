// Desktop Connection card — switch between local and remote engine.
// Only visible when running inside the Tauri desktop app (detected via
// window.__TAURI__). In a browser, this card is hidden.
//
// Local mode: the desktop app spawns a bundled sidecar engine.
// Remote mode: the desktop app connects to a remote engine URL (e.g. a
// VPS on tailnet). Changes take effect on next app launch.

import { useCallback, useEffect, useState } from 'react';
import { useAsync } from '../useAsync';

interface DesktopConnection {
  mode: string;
  remote_url: string;
  from_env: boolean;
}

// Type for Tauri's invoke function — loaded dynamically since it only
// exists inside the desktop app.
type InvokeFn = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

function getInvoke(): InvokeFn | null {
  const w = window as unknown as {
    __TAURI__?: { core?: { invoke?: InvokeFn } };
  };
  return w.__TAURI__?.core?.invoke ?? null;
}

function isTauri(): boolean {
  return getInvoke() !== null;
}

const connectionLoader = async (): Promise<DesktopConnection> => {
  const invoke = getInvoke();
  if (!invoke) throw new Error('not in Tauri');
  return invoke('get_desktop_connection') as Promise<DesktopConnection>;
};

export function DesktopConnectionCard() {
  const invoke = getInvoke();
  const conn = useAsync<DesktopConnection>(connectionLoader);

  const [url, setUrl] = useState('');
  const [result, setResult] = useState<string | null>(null);
  const [resultOk, setResultOk] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (conn.state === 'ready' && conn.data) {
      setUrl(conn.data.remote_url || '');
    }
  }, [conn.data, conn.state]);

  const onSave = useCallback(async () => {
    if (!invoke) return;
    if (!url.trim()) {
      setResult('URL is required for remote mode.');
      setResultOk(false);
      return;
    }
    setResult(null);
    setSubmitting(true);
    try {
      const r = (await invoke('set_remote_url', { url: url.trim() })) as DesktopConnection;
      setResult(`✓ Saved — remote mode will activate on next launch: ${r.remote_url}`);
      setResultOk(true);
      conn.reload();
    } catch (err) {
      setResult(err instanceof Error ? err.message : 'save failed');
      setResultOk(false);
    } finally {
      setSubmitting(false);
    }
  }, [url, invoke, conn]);

  const onClear = useCallback(async () => {
    if (!invoke) return;
    setResult(null);
    setSubmitting(true);
    try {
      await invoke('clear_remote_url');
      setResult('✓ Cleared — local mode will activate on next launch.');
      setResultOk(true);
      setUrl('');
      conn.reload();
    } catch (err) {
      setResult(err instanceof Error ? err.message : 'clear failed');
      setResultOk(false);
    } finally {
      setSubmitting(false);
    }
  }, [invoke, conn]);

  // Don't render at all in a browser (non-Tauri context).
  if (!isTauri()) return null;

  const data = conn.data;
  const isRemote = data?.mode === 'remote';

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Desktop Connection</h2>
        <span
          className={
            'settings__badge ' +
            (isRemote ? 'settings__badge--ok' : 'settings__badge--off')
          }
        >
          {isRemote ? 'remote' : 'local'}
        </span>
      </header>

      <p className="settings__hint">
        {isRemote
          ? 'The desktop app connects to a remote engine. Changes take effect on next launch.'
          : 'The desktop app runs a local bundled engine. Switch to remote to connect to a VPS or tailnet server.'}
      </p>

      {data?.from_env && (
        <p className="settings__meta">
          ⚠ URL is set via KOMPANY_REMOTE_URL env var — UI changes won't override it
          until the env var is unset.
        </p>
      )}

      <label className="settings__field">
        <span className="settings__label">Remote engine URL</span>
        <input
          className="settings__input"
          type="text"
          value={url}
          placeholder="http://your-server:55352"
          onChange={(e) => setUrl(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          disabled={data?.from_env}
        />
      </label>

      <div className="settings__actions">
        <button
          className="btn btn--primary btn--sm"
          onClick={onSave}
          disabled={submitting || data?.from_env}
        >
          {submitting ? 'Saving…' : 'Save (remote)'}
        </button>
        {isRemote && !data?.from_env && (
          <button
            className="btn btn--ghost btn--sm"
            onClick={onClear}
            disabled={submitting}
          >
            Switch to local
          </button>
        )}
        {result && (
          <span
            className={
              'settings__result ' +
              (resultOk ? 'settings__result--ok' : 'settings__result--err')
            }
          >
            {result}
          </span>
        )}
      </div>
    </section>
  );
}
