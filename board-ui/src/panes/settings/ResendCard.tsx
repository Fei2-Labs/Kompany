// Resend card — recommended email integration (native API, better
// deliverability + delivery ids than raw SMTP).

import { useCallback, useState } from 'react';
import {
  connectResend,
  getIntegrations,
  getResendCredentials,
  sendTestEmail,
  type ResendCredentials,
} from '../../api/client';
import { useAsync } from '../useAsync';

interface ResendStatus {
  connected: boolean;
  creds: ResendCredentials;
}

async function loadResendStatus(signal?: AbortSignal): Promise<ResendStatus> {
  const [list, creds] = await Promise.all([
    getIntegrations(signal),
    getResendCredentials(signal),
  ]);
  const connected = list.some((i) => i.integration_id === 'resend' && i.connected);
  return { connected, creds };
}

export function ResendCard() {
  const status = useAsync<ResendStatus>(loadResendStatus);

  const [apiKey, setApiKey] = useState('');
  const [from, setFrom] = useState('');
  const [testTo, setTestTo] = useState('');
  const [connecting, setConnecting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  const creds = status.data?.creds;

  const onConnect = useCallback(async () => {
    const fromTrimmed = from.trim() || creds?.resend_from || '';
    if (!fromTrimmed) {
      setResult({ ok: false, detail: 'from required' });
      return;
    }
    setConnecting(true);
    setResult({ ok: true, detail: 'verifying…' });
    try {
      const r = await connectResend({ api_key: apiKey.trim(), resend_from: fromTrimmed });
      setResult(r);
      if (r.ok) {
        status.reload();
        setApiKey('');
      }
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'connect failed',
      });
    } finally {
      setConnecting(false);
    }
  }, [apiKey, from, creds, status]);

  const onTest = useCallback(async () => {
    setTesting(true);
    setResult({ ok: true, detail: testTo ? `sending test to ${testTo}…` : 'sending test…' });
    try {
      const r = await sendTestEmail(testTo.trim());
      setResult(r);
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'test failed',
      });
    } finally {
      setTesting(false);
    }
  }, [testTo]);

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Email — Resend (recommended)</h2>
        <span
          className={
            'settings__badge ' +
            (status.data?.connected ? 'settings__badge--ok' : 'settings__badge--off')
          }
        >
          {status.data?.connected ? 'connected' : 'not connected'}
        </span>
      </header>
      <p className="settings__hint">
        Connect Resend (API) — preferred over raw SMTP for deliverability +
        delivery ids. Paste your Resend API key and a verified sender
        address.
      </p>

      <label className="settings__field">
        <span className="settings__label">Resend API key</span>
        <input
          className="settings__input"
          type="password"
          value={apiKey}
          placeholder={
            creds?.resend_api_key_set
              ? `${creds.resend_api_key_mask} — leave blank to keep`
              : 're_...'
          }
          onChange={(e) => setApiKey(e.target.value)}
          autoComplete="off"
        />
      </label>

      <label className="settings__field">
        <span className="settings__label">From (verified sender)</span>
        <input
          className="settings__input"
          value={from || creds?.resend_from || ''}
          placeholder="you@yourdomain.com"
          onChange={(e) => setFrom(e.target.value)}
          autoComplete="off"
        />
      </label>

      <label className="settings__field">
        <span className="settings__label">Send test to</span>
        <input
          className="settings__input"
          value={testTo}
          placeholder="defaults to the From address"
          onChange={(e) => setTestTo(e.target.value)}
          autoComplete="off"
        />
      </label>

      <div className="settings__actions">
        <button
          className="btn btn--primary btn--sm"
          onClick={onConnect}
          disabled={connecting}
        >
          {connecting ? 'Verifying…' : 'Connect + Verify'}
        </button>
        <button className="btn btn--ghost btn--sm" onClick={onTest} disabled={testing}>
          {testing ? 'Sending…' : 'Send test'}
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
