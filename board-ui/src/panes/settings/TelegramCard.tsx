// Telegram card — CEO channel bot token + allowed chat ids. The engine
// verifies the token via `getMe` before storing it (bad token caught now,
// not at worker start).

import { useCallback, useEffect, useState } from 'react';
import {
  connectTelegram,
  getChannelsStatus,
  getTelegramCredentials,
  type ConnectResult,
} from '../../api/client';
import { useAsync } from '../useAsync';

const telegramLoader = (signal?: AbortSignal) => getTelegramCredentials(signal);
const statusLoader = (signal?: AbortSignal) => getChannelsStatus(signal);

export function TelegramCard() {
  const creds = useAsync(telegramLoader);
  const status = useAsync(statusLoader);

  const [token, setToken] = useState('');
  const [chats, setChats] = useState('');
  const [tokenSaved, setTokenSaved] = useState(false);
  const [tokenMask, setTokenMask] = useState('');
  const [result, setResult] = useState<ConnectResult | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (creds.state === 'ready' && creds.data) {
      setTokenSaved(creds.data.telegram_bot_token_set);
      setTokenMask(creds.data.telegram_bot_token_mask ?? '');
      setChats(creds.data.telegram_allowed_chat_ids ?? '');
      setToken('');
    }
  }, [creds.data, creds.state]);

  const onSubmit = useCallback(async () => {
    if (!chats.trim()) {
      setResult({ ok: false, detail: 'Allowed chat IDs required.' });
      return;
    }
    if (!token.trim() && !tokenSaved) {
      setResult({ ok: false, detail: 'Bot token required.' });
      return;
    }
    setResult(null);
    setSubmitting(true);
    try {
      const r = await connectTelegram({
        bot_token: token.trim(),
        allowed_chat_ids: chats.trim(),
      });
      setResult(r);
      if (r.ok) {
        creds.reload();
        status.reload();
        setToken('');
      }
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'connect failed',
      });
    } finally {
      setSubmitting(false);
    }
  }, [chats, token, tokenSaved, creds, status]);

  const tg = status.data?.telegram;

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Telegram</h2>
        <span
          className={
            'settings__badge ' +
            (tokenSaved ? 'settings__badge--ok' : 'settings__badge--off')
          }
        >
          {tokenSaved ? 'configured' : 'not configured'}
        </span>
      </header>

      <p className="settings__hint">
        Create a bot with{' '}
        <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">
          @BotFather
        </a>{' '}
        <code>/newbot</code>, then get your chat id from{' '}
        <a href="https://t.me/userinfobot" target="_blank" rel="noreferrer">
          @userinfobot
        </a>
        . Multiple chat ids: comma-separated.
      </p>

      {tg?.configured && (
        <p className="settings__meta">
          worker {tg.running ? 'running' : 'stopped'}
          {tg.last_update_at ? ` · last update ${tg.last_update_at}` : ''}
          {tg.updates_handled ? ` · ${tg.updates_handled} handled` : ''}
        </p>
      )}

      <label className="settings__field">
        <span className="settings__label">Bot token</span>
        <input
          className="settings__input"
          type="password"
          value={token}
          placeholder={
            tokenSaved
              ? `${tokenMask} — leave blank to keep`
              : '123456:ABC-DEF...'
          }
          onChange={(e) => setToken(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
      </label>

      <label className="settings__field">
        <span className="settings__label">Allowed chat IDs</span>
        <input
          className="settings__input"
          type="text"
          value={chats}
          placeholder="111,-100222"
          onChange={(e) => setChats(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
      </label>

      <div className="settings__actions">
        <button
          className="btn btn--primary btn--sm"
          onClick={onSubmit}
          disabled={submitting}
        >
          {submitting ? 'Verifying…' : 'Connect + Verify'}
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
