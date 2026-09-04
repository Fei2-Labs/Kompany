// Workspaces card — one isolated workspace per brand (own database, vault,
// ledger, integrations; nothing shared). Switching rebinds the server to
// the brand's data dir and reloads the page.

import { useCallback, useState } from 'react';
import {
  createWorkspace,
  getWorkspaces,
  switchWorkspace,
  type WorkspaceEntry,
  type WorkspacesList,
} from '../../api/client';
import { useAsync } from '../useAsync';

const workspacesLoader = (signal?: AbortSignal) => getWorkspaces(signal);

function WorkspaceRow({
  ws,
  onResult,
}: {
  ws: WorkspaceEntry;
  onResult: (r: { ok: boolean; detail: string }) => void;
}) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);

  const onSwitch = useCallback(async () => {
    if (!armed) {
      setArmed(true);
      onResult({ ok: true, detail: `click again to switch to '${ws.name}' — the page reloads` });
      return;
    }
    setBusy(true);
    try {
      const res = await switchWorkspace(ws.name);
      if (res.error) {
        onResult({ ok: false, detail: res.error });
        return;
      }
      if (res.restart_required) {
        onResult({
          ok: true,
          detail:
            'switched in the registry — this server is pinned by KOMPANY_DATA_DIR; restart it to pick up the new brand',
        });
        return;
      }
      onResult({ ok: true, detail: 'switched — reloading…' });
      window.location.reload();
    } catch (err) {
      onResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'switch failed',
      });
    } finally {
      setBusy(false);
      setArmed(false);
    }
  }, [armed, ws.name, onResult]);

  return (
    <div className="settings__row">
      <div className="settings__row-label">
        {ws.active ? '▸ ' : ''}
        {ws.name}
        {ws.label && ws.label !== ws.name ? ` — ${ws.label}` : ''}
        <span className="settings__row-sub">{ws.data_dir}</span>
      </div>
      {!ws.active && (
        <div className="settings__row-actions">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={onSwitch}
            disabled={busy}
          >
            {armed ? 'Confirm — switches the whole app' : 'Switch'}
          </button>
        </div>
      )}
    </div>
  );
}

export function WorkspacesCard() {
  const workspaces = useAsync<WorkspacesList>(workspacesLoader);
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  const onCreate = useCallback(async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setResult({ ok: false, detail: 'workspace name required' });
      return;
    }
    setCreating(true);
    setResult(null);
    try {
      const res = await createWorkspace(trimmed);
      if (res.error) {
        setResult({ ok: false, detail: res.error });
        return;
      }
      setResult({
        ok: true,
        detail: `created '${res.name}' — switch to it, then onboard the new brand`,
      });
      setName('');
      workspaces.reload();
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'create failed',
      });
    } finally {
      setCreating(false);
    }
  }, [name, workspaces]);

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Workspaces</h2>
      </header>
      <p className="settings__hint">
        One isolated workspace per brand — own database, vault, ledger,
        integrations. Nothing is shared. Switching rebinds this server to
        the brand's data dir and reloads the page.
      </p>

      {workspaces.state === 'loading' ? (
        <p className="settings__meta">loading…</p>
      ) : workspaces.state === 'error' ? (
        <p className="settings__meta">{workspaces.error ?? 'failed to load workspaces'}</p>
      ) : (
        <>
          <div>
            {workspaces.data?.workspaces.map((ws) => (
              <WorkspaceRow key={ws.name} ws={ws} onResult={setResult} />
            ))}
          </div>
          {workspaces.data?.env_override && (
            <p className="settings__meta">
              KOMPANY_DATA_DIR is set — it bypasses the registry; switching here will not
              affect this server until that env var is removed.
            </p>
          )}
        </>
      )}

      <label className="settings__field">
        <span className="settings__label">New workspace</span>
        <input
          className="settings__input"
          value={name}
          placeholder="brand-name (lowercase)"
          onChange={(e) => setName(e.target.value)}
          autoComplete="off"
        />
      </label>
      <div className="settings__actions">
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onCreate}
          disabled={creating}
        >
          {creating ? 'Creating…' : '+ create workspace'}
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
