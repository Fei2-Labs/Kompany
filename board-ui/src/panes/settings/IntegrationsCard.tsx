// Integrations card — everything the team can connect to (loader-driven:
// builtins + plugins). Connected = all required credentials are in the
// encrypted vault. Connect fills the missing credentials inline; disconnect
// clears them (two-stage confirm, no native dialogs in the Tauri WebView).

import { useCallback, useState } from 'react';
import {
  deleteCredential,
  getCredentials,
  getIntegrations,
  setCredential,
  type IntegrationInfo,
} from '../../api/client';
import { useAsync } from '../useAsync';

interface IntegrationsData {
  list: IntegrationInfo[];
  vaultNames: Set<string>;
}

async function loadIntegrationsData(signal?: AbortSignal): Promise<IntegrationsData> {
  const [list, creds] = await Promise.all([
    getIntegrations(signal),
    getCredentials(signal),
  ]);
  return { list, vaultNames: new Set(creds.map((c) => c.name)) };
}

function IntegrationRow({
  integ,
  vaultNames,
  onChanged,
}: {
  integ: IntegrationInfo;
  vaultNames: Set<string>;
  onChanged: () => void;
}) {
  const [formOpen, setFormOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  const missing = integ.required_credentials.filter((n) => !vaultNames.has(n));
  const fieldsToShow = missing.length ? missing : integ.required_credentials;

  const onSaveCreds = useCallback(async () => {
    setBusy(true);
    setResult(null);
    try {
      for (const name of fieldsToShow) {
        const value = (values[name] ?? '').trim();
        if (!value) continue;
        await setCredential(name, value);
      }
      setResult({ ok: true, detail: `credentials saved for ${integ.display_name}` });
      setFormOpen(false);
      setValues({});
      onChanged();
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'save failed',
      });
    } finally {
      setBusy(false);
    }
  }, [fieldsToShow, values, integ.display_name, onChanged]);

  const onDisconnect = useCallback(async () => {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      for (const name of integ.required_credentials) {
        await deleteCredential(name);
      }
      setResult({ ok: true, detail: `${integ.display_name} disconnected` });
      onChanged();
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'disconnect failed',
      });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }, [confirming, integ, onChanged]);

  return (
    <div className="settings__row">
      <div className="settings__row-label">
        <strong>{integ.display_name}</strong>{' '}
        <span
          className={
            'settings__badge ' +
            (integ.connected ? 'settings__badge--ok' : 'settings__badge--off')
          }
        >
          {integ.connected ? 'connected' : 'not connected'}
        </span>
        <span className="settings__row-sub">
          {integ.description}
          {integ.description ? ' · ' : ''}
          credentials: {integ.required_credentials.join(', ') || 'none'}
          {integ.tools.length ? ` · tools: ${integ.tools.join(', ')}` : ''}
        </span>
        {result && (
          <span
            className={
              'settings__result ' +
              (result.ok ? 'settings__result--ok' : 'settings__result--err')
            }
          >
            {result.ok ? ' ✓ ' : ' ✗ '}
            {result.detail}
          </span>
        )}
      </div>
      <div className="settings__row-actions">
        {integ.connected ? (
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={onDisconnect}
            disabled={busy}
          >
            {confirming ? 'Confirm — clears stored credentials' : 'Disconnect'}
          </button>
        ) : (
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={() => setFormOpen((v) => !v)}
          >
            Connect
          </button>
        )}
      </div>
      {formOpen && !integ.connected && (
        <div className="settings__inline-form">
          {fieldsToShow.map((name) => (
            <label className="settings__field" key={name}>
              <span className="settings__label">{name}</span>
              <input
                className="settings__input"
                type={/key|password|token|secret/i.test(name) ? 'password' : 'text'}
                value={values[name] ?? ''}
                onChange={(e) => setValues((v) => ({ ...v, [name]: e.target.value }))}
                autoComplete="off"
              />
            </label>
          ))}
          <div className="settings__actions">
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={onSaveCreds}
              disabled={busy}
            >
              {busy ? 'Saving…' : 'Save credentials'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function IntegrationsCard() {
  const data = useAsync<IntegrationsData>(loadIntegrationsData);

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Integrations</h2>
      </header>
      <p className="settings__hint">
        Everything the team can connect to. Connected = all required
        credentials are in the encrypted vault.
      </p>

      {data.state === 'loading' ? (
        <p className="settings__meta">loading…</p>
      ) : data.state === 'error' ? (
        <p className="settings__meta">{data.error ?? 'integrations unavailable'}</p>
      ) : !data.data?.list.length ? (
        <p className="settings__meta">no integrations registered</p>
      ) : (
        <div>
          {data.data.list.map((integ) => (
            <IntegrationRow
              key={integ.integration_id}
              integ={integ}
              vaultNames={data.data!.vaultNames}
              onChanged={() => data.reload()}
            />
          ))}
        </div>
      )}
    </section>
  );
}
