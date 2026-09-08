// Update card — installed vs latest GitHub release, one-button apply with live
// phase, mode (manual / automatic_when_idle), rollback. Backed by /update/*.

import { useCallback, useEffect, useRef, useState } from 'react';

interface ReleaseRow {
  version?: string;
  tag?: string;
  html_url?: string;
}

interface UpdateStatus {
  phase: string;
  mode: string;
  installed_version: string | null;
  installed_pro_version: string | null;
  target_version: string | null;
  previous_version: string | null;
  latest: Record<string, ReleaseRow>;
  update_available: boolean;
  last_check_at: string | null;
  steps: { at: string; step: string; detail: string }[];
  attestation: { status?: string; detail?: string };
  error: string | null;
  restart_required: boolean;
  can_apply: boolean;
  cannot_apply_reason: string | null;
  supervised: string | null;
  layout: { running_from_release: boolean; frozen: boolean; current: string | null };
}

const BUSY = new Set(['checking', 'backing_up', 'downloading', 'verifying', 'installing', 'switching']);

async function api<T>(path: string, method: 'GET' | 'POST' = 'GET', body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { accept: 'application/json', ...(body ? { 'content-type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}`);
  return (await res.json()) as T;
}

export function UpdateCard() {
  const [st, setSt] = useState<UpdateStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSt(await api<UpdateStatus>('/update'));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'failed to load');
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [refresh]);

  // Poll while an update is in flight (the engine restarts at the end; the
  // page reloads itself when /update answers again on the new version).
  useEffect(() => {
    const inFlight = st ? BUSY.has(st.phase) || st.phase === 'restarting' : false;
    if (inFlight && !timer.current) {
      timer.current = window.setInterval(() => void refresh(), 2000);
    } else if (!inFlight && timer.current) {
      window.clearInterval(timer.current);
      timer.current = null;
    }
  }, [st, refresh]);

  const run = useCallback(
    async (path: string, body?: unknown) => {
      setBusy(true);
      try {
        setSt(await api<UpdateStatus>(path, 'POST', body));
        setErr(null);
      } catch (e) {
        setErr(e instanceof Error ? e.message : 'request failed');
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const latest = st?.latest?.kompany?.version;
  const inFlight = st ? BUSY.has(st.phase) || st.phase === 'restarting' : false;
  const lastStep = st?.steps?.length ? st.steps[st.steps.length - 1] : null;

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Update</h2>
      </header>
      <p className="settings__hint">
        Updates come only from signed GitHub releases: verified wheels into a fresh release
        directory, a database backup, then a restart. The first boot rolls back on its own if
        the doctor fails.
      </p>
      {err && <p className="settings__meta">{err}</p>}
      {st && (
        <>
          <p className="settings__meta">
            installed: kompany {st.installed_version}
            {st.installed_pro_version ? ` · pro ${st.installed_pro_version}` : ''}
            {' — latest: '}
            {latest ? `kompany ${latest}` : 'not checked yet'}
            {st.last_check_at ? ` (checked ${st.last_check_at.slice(0, 16).replace('T', ' ')})` : ''}
          </p>
          {st.update_available && <p className="settings__meta">✦ update available: {latest}</p>}
          {!st.can_apply && <p className="settings__meta">{st.cannot_apply_reason}</p>}
          {inFlight && (
            <p className="settings__meta">
              {st.phase}
              {st.target_version ? ` → ${st.target_version}` : ''}
              {lastStep ? ` · ${lastStep.step}: ${lastStep.detail}` : ''}
              {st.phase === 'restarting' ? ' · the engine is restarting, this page reconnects by itself' : ''}
            </p>
          )}
          {st.phase === 'done' && <p className="settings__result settings__result--ok">✓ running {st.installed_version}</p>}
          {(st.phase === 'failed' || st.phase === 'rolled_back') && st.error && (
            <p className="settings__result settings__result--err">✗ {st.error}</p>
          )}
          {st.attestation?.status && <p className="settings__meta">provenance: {st.attestation.status}</p>}
          <div className="settings__actions">
            <button className="btn btn--sm" onClick={() => void run('/update/check')} disabled={busy || inFlight}>
              Check now
            </button>
            <button
              className="btn btn--primary btn--sm"
              onClick={() => void run('/update/apply', {})}
              disabled={busy || inFlight || !st.can_apply || !st.update_available}
            >
              {latest && st.update_available ? `Update to ${latest}` : 'Up to date'}
            </button>
            {st.previous_version && st.can_apply && (
              <button className="btn btn--sm" onClick={() => void run('/update/rollback')} disabled={busy || inFlight}>
                Roll back to {st.previous_version}
              </button>
            )}
          </div>
          <label className="settings__field">
            <span className="settings__label">Mode</span>
            <select
              className="settings__select"
              value={st.mode}
              onChange={(e) => void run('/update/mode', { mode: e.target.value })}
              disabled={busy || inFlight}
            >
              <option value="manual">manual — tell me, I click</option>
              <option value="automatic_when_idle">automatic when idle — install when no agent is working</option>
            </select>
          </label>
        </>
      )}
    </section>
  );
}
