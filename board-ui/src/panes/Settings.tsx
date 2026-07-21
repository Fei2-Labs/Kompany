// Settings pane — integration credentials (Telegram first).
//
// Mirrors the legacy /ui/settings.html forms but in the multica board
// shell. Each integration is a card with status + form fields + a
// verify-then-store button. The engine validates tokens before saving
// (Telegram: getMe, Resend: /domains, Email: SMTP login) so a bad
// token never lands in the vault.
//
// Future: Resend + Email SMTP cards reuse the same shape — add them
// here when the board needs them. For now Telegram is the only one
// wired because that's what the founder asked for first.

import { useCallback, useEffect, useState } from 'react';
import {
  getTelegramCredentials,
  connectTelegram,
  type TelegramCredentials,
  type ConnectResult,
} from '../api/client';
import { useAsync } from './useAsync';
import { PaneShell } from './PaneShell';

const telegramLoader = (signal?: AbortSignal) => getTelegramCredentials(signal);

export function Settings() {
  // No SSE refetch — Settings is a write surface, we reload imperatively
  // after a successful connect.
  const creds = useAsync<TelegramCredentials>(telegramLoader);

  // Local form state — populated from the credentials fetch so the
  // founder can edit chat ids without retyping the token.
  const [token, setToken] = useState('');
  const [chats, setChats] = useState('');
  const [tokenSaved, setTokenSaved] = useState(false);
  const [tokenMask, setTokenMask] = useState('');

  // Result line — ok=true green, ok=false red. Cleared on next submit.
  const [result, setResult] = useState<ConnectResult | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (creds.state === 'ready' && creds.data) {
      setTokenSaved(creds.data.telegram_bot_token_set);
      setTokenMask(creds.data.telegram_bot_token_mask ?? '');
      setChats(creds.data.telegram_allowed_chat_ids ?? '');
      // Don't prefill the token — it's masked server-side. The founder
      // types a new one or leaves it blank to keep the saved one.
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
        // Refetch so the status line + mask update.
        creds.reload();
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
  }, [chats, token, tokenSaved, creds]);

  const ready = creds.state === 'ready';

  return (
    <PaneShell
      title="Settings"
      subtitle="Integration credentials — verified before they hit the vault."
      state={creds.state === 'error' ? 'error' : ready ? 'ready' : 'loading'}
      error={creds.error}
    >
      <div className="settings">
        {/* ---- Telegram ---------------------------------------------- */}
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

        {/* ---- Future: Resend, Email SMTP ---------------------------- */}
        {/* Add cards here when the founder needs them in the board SPA.
            The legacy /ui/settings.html forms still work in the meantime. */}
      </div>
    </PaneShell>
  );
}
