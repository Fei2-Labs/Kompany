// Email (SMTP) card — alternative to Resend. Works with Gmail
// app-passwords or any SMTP provider. Stored encrypted in the vault.

import { useCallback, useState } from 'react';
import {
  connectEmailSmtp,
  getEmailSmtpCredentials,
  getIntegrations,
  sendTestEmail,
  type EmailSmtpCredentials,
} from '../../api/client';
import { useAsync } from '../useAsync';

interface EmailSmtpStatus {
  connected: boolean;
  creds: EmailSmtpCredentials;
}

async function loadEmailSmtpStatus(signal?: AbortSignal): Promise<EmailSmtpStatus> {
  const [list, creds] = await Promise.all([
    getIntegrations(signal),
    getEmailSmtpCredentials(signal),
  ]);
  const connected = list.some((i) => i.integration_id === 'email_smtp' && i.connected);
  return { connected, creds };
}

export function EmailSmtpCard() {
  const status = useAsync<EmailSmtpStatus>(loadEmailSmtpStatus);

  const [host, setHost] = useState('');
  const [port, setPort] = useState('587');
  const [user, setUser] = useState('');
  const [password, setPassword] = useState('');
  const [from, setFrom] = useState('');
  const [testTo, setTestTo] = useState('');
  const [connecting, setConnecting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  const creds = status.data?.creds;

  const onConnect = useCallback(async () => {
    const hostTrimmed = host.trim() || creds?.smtp_host || '';
    const userTrimmed = user.trim() || creds?.smtp_user || '';
    if (!hostTrimmed || !userTrimmed || !password) {
      setResult({ ok: false, detail: 'host + user + password required' });
      return;
    }
    setConnecting(true);
    setResult({ ok: true, detail: 'verifying…' });
    try {
      const r = await connectEmailSmtp({
        smtp_host: hostTrimmed,
        smtp_port: port.trim() || '587',
        smtp_user: userTrimmed,
        smtp_password: password,
        smtp_from: from.trim(),
      });
      setResult(r);
      if (r.ok) {
        status.reload();
        setPassword('');
      }
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'connect failed',
      });
    } finally {
      setConnecting(false);
    }
  }, [host, port, user, password, from, creds, status]);

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
        <h2 className="settings__card-title">Email (SMTP) — alternative</h2>
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
        Connect an email account so the team can send outreach — not just
        draft it. Works with Gmail app-passwords or any SMTP provider.
      </p>

      <div className="settings__grid">
        <label className="settings__field">
          <span className="settings__label">SMTP host</span>
          <input
            className="settings__input"
            value={host || creds?.smtp_host || ''}
            placeholder="smtp.gmail.com"
            onChange={(e) => setHost(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label className="settings__field">
          <span className="settings__label">Port</span>
          <input
            className="settings__input"
            value={port}
            onChange={(e) => setPort(e.target.value)}
            autoComplete="off"
          />
        </label>
      </div>

      <label className="settings__field">
        <span className="settings__label">User (email)</span>
        <input
          className="settings__input"
          value={user || creds?.smtp_user || ''}
          placeholder="you@gmail.com"
          onChange={(e) => setUser(e.target.value)}
          autoComplete="off"
        />
      </label>

      <label className="settings__field">
        <span className="settings__label">Password / app-password</span>
        <input
          className="settings__input"
          type="password"
          value={password}
          placeholder={
            creds?.smtp_password_set
              ? `${creds.smtp_password_mask} — leave blank to keep`
              : ''
          }
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="off"
        />
      </label>

      <label className="settings__field">
        <span className="settings__label">From (optional)</span>
        <input
          className="settings__input"
          value={from || creds?.smtp_from || ''}
          placeholder="defaults to user"
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
