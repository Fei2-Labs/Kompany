// Credentials vault card — raw vault entries (values stay encrypted, never
// shown). Update writes a new value; delete removes the entry (the
// integration using it shows as not connected). Rotate re-encrypts every
// stored credential with a new Fernet key.

import { useCallback, useState } from 'react';
import {
  deleteCredential,
  getCredentials,
  rotateCredentialKey,
  setCredential,
  type CredentialEntry,
} from '../../api/client';
import { useAsync } from '../useAsync';

const credentialsLoader = (signal?: AbortSignal) => getCredentials(signal);

function CredentialRow({
  entry,
  onChanged,
}: {
  entry: CredentialEntry;
  onChanged: () => void;
}) {
  const [value, setValue] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  const onUpdate = useCallback(async () => {
    const trimmed = value.trim();
    if (!trimmed) {
      setResult({ ok: false, detail: 'type a new value first' });
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      await setCredential(entry.name, trimmed);
      setResult({ ok: true, detail: `${entry.name} updated` });
      setValue('');
      onChanged();
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'update failed',
      });
    } finally {
      setBusy(false);
    }
  }, [value, entry.name, onChanged]);

  const onDelete = useCallback(async () => {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      await deleteCredential(entry.name);
      setResult({ ok: true, detail: `${entry.name} deleted` });
      onChanged();
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'delete failed',
      });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }, [confirming, entry.name, onChanged]);

  return (
    <div className="settings__row">
      <div className="settings__row-label" title={entry.updated_at ? `updated ${entry.updated_at}` : ''}>
        {entry.name}
      </div>
      <input
        className="settings__input settings__row-input"
        type="password"
        placeholder="•••••• — type to replace"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        autoComplete="off"
      />
      <div className="settings__row-actions">
        <button className="btn btn--primary btn--sm" onClick={onUpdate} disabled={busy}>
          Update
        </button>
        <button className="btn btn--ghost btn--sm" onClick={onDelete} disabled={busy}>
          {confirming ? 'Confirm' : '×'}
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
    </div>
  );
}

export function CredentialsCard() {
  const creds = useAsync<CredentialEntry[]>(credentialsLoader);
  const [newKey, setNewKey] = useState('');
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [rotateResult, setRotateResult] = useState<{ ok: boolean; detail: string } | null>(
    null,
  );

  const onRotate = useCallback(async () => {
    const trimmed = newKey.trim();
    if (!trimmed) {
      setRotateResult({ ok: false, detail: 'new vault key required' });
      return;
    }
    if (!confirmRotate) {
      setConfirmRotate(true);
      setRotateResult({
        ok: true,
        detail: 'click again to confirm key rotation — update KOMPANY_VAULT_KEY afterwards',
      });
      return;
    }
    setRotating(true);
    setRotateResult(null);
    try {
      const d = await rotateCredentialKey(trimmed);
      setRotateResult({
        ok: true,
        detail: `key rotated (${d.rotated ?? '?'} entries) — set KOMPANY_VAULT_KEY to the new key`,
      });
      setNewKey('');
    } catch (err) {
      setRotateResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'rotation failed',
      });
    } finally {
      setRotating(false);
      setConfirmRotate(false);
    }
  }, [newKey, confirmRotate]);

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Credentials</h2>
      </header>
      <p className="settings__hint">
        Raw vault entries (values stay encrypted — never shown). Update
        writes a new value; delete removes the entry (the integration using
        it shows as not connected).
      </p>

      {creds.state === 'loading' ? (
        <p className="settings__meta">loading…</p>
      ) : creds.state === 'error' ? (
        <p className="settings__meta">{creds.error ?? 'credentials unavailable'}</p>
      ) : !creds.data?.length ? (
        <p className="settings__meta">vault is empty</p>
      ) : (
        <div>
          {creds.data.map((entry) => (
            <CredentialRow key={entry.name} entry={entry} onChanged={() => creds.reload()} />
          ))}
        </div>
      )}

      <p className="settings__meta">
        Rotate vault key — re-encrypts every stored credential with a new
        key. Save the new key: it replaces KOMPANY_VAULT_KEY.
      </p>
      <label className="settings__field">
        <span className="settings__label">New vault key</span>
        <input
          className="settings__input"
          type="password"
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
          autoComplete="off"
        />
      </label>
      <div className="settings__actions">
        <button className="btn btn--ghost btn--sm" onClick={onRotate} disabled={rotating}>
          {rotating
            ? 'Rotating…'
            : confirmRotate
              ? 'Confirm — re-encrypts the whole vault'
              : 'Rotate key'}
        </button>
        {rotateResult && (
          <span
            className={
              'settings__result ' +
              (rotateResult.ok ? 'settings__result--ok' : 'settings__result--err')
            }
          >
            {rotateResult.ok ? '✓ ' : '✗ '}
            {rotateResult.detail}
          </span>
        )}
      </div>
    </section>
  );
}
