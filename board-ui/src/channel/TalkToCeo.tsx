// `kompany>` Talk-to-CEO pane: founder states intent, the CEO routes it. Renders
// the multi-turn turn thread (founder / ceo, kind, cost), handles clarify replies
// on the same session, GO / Abandon on a gated/proposed session, and shows per-turn
// + total AI cost. Failures render the classified error_code gracefully — never a
// raw 500. No native dialogs (inline UI, Tauri WebView).

import { useEffect, useRef, useState } from 'react';
import { moneyCents } from '../board/format';
import { errorLabel, isTerminal } from './state';
import { useChannel, type UseChannel } from './useChannel';

/** Optional shared channel (App passes one so ⌘K can focus the input). */
interface TalkToCeoProps {
  channel?: UseChannel;
  /** Set true by the palette "send to CEO" action to focus the composer. */
  focusSignal?: number;
}

export function TalkToCeo({ channel, focusSignal }: TalkToCeoProps) {
  // Fall back to a local channel if none is shared.
  const local = useChannel();
  const ch = channel ?? local;
  const [text, setText] = useState('');
  const [quick, setQuick] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (focusSignal !== undefined) inputRef.current?.focus();
  }, [focusSignal]);

  // Auto-scroll the thread on new turns.
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [ch.turns.length]);

  const submit = async () => {
    const t = text.trim();
    if (!t || ch.busy) return;
    setText('');
    if (quick) await ch.quickSend(t);
    else await ch.send(t);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends; Shift+Enter newlines. ⌘/Ctrl+Enter also sends.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  };

  const terminal = isTerminal(ch.phase);
  const placeholder = ch.canReply
    ? 'Reply to the CEO…'
    : quick
      ? 'Quick directive (fire-and-forget)…'
      : 'State your intent. The CEO routes it.';

  return (
    <section className="pane ceo">
      <header className="pane__header ceo__header">
        <h1 className="pane__title">kompany&gt;</h1>
        <div className="ceo__head-right">
          <span className="ceo__cost">AI cost: {moneyCents(ch.totalCost)}</span>
          {ch.turns.length > 0 && (
            <button type="button" className="ceo__reset" onClick={ch.reset}>
              New thread
            </button>
          )}
        </div>
      </header>

      <div className="ceo__thread" ref={threadRef}>
        {ch.turns.length === 0 && (
          <p className="ceo__hint">
            Tell the CEO what you want. They&apos;ll ask back if it&apos;s unclear,
            or hold the action for your GO when it crosses a gate.
          </p>
        )}
        {ch.turns.map((t) => (
          <div key={t.id} className={`turn turn--${t.role}`}>
            <div className="turn__meta">
              <span className="turn__role">
                {t.role === 'ceo' ? (t.agentId ?? 'ceo').toUpperCase() : 'You'}
              </span>
              {t.kind !== 'message' && (
                <span className="turn__kind">{t.kind.replace(/_/g, ' ')}</span>
              )}
              {t.cost > 0 && (
                <span className="turn__cost">{moneyCents(t.cost)}</span>
              )}
            </div>
            <div className="turn__body">{t.text}</div>
          </div>
        ))}
      </div>

      {ch.phase === 'terminal-failed' && ch.last?.error_code && (
        <div className="ceo__error">{errorLabel(ch.last.error_code)}</div>
      )}
      {ch.error && <div className="ceo__error">{ch.error}</div>}

      {ch.canGo ? (
        <div className="ceo__gate">
          <span className="ceo__gate-note">
            The CEO is holding this action for your decision.
          </span>
          <div className="ceo__gate-actions">
            <button
              type="button"
              className="btn btn--primary"
              disabled={ch.busy}
              onClick={() => void ch.go()}
            >
              GO
            </button>
            <button
              type="button"
              className="btn"
              disabled={ch.busy}
              onClick={() => void ch.abandon()}
            >
              Abandon
            </button>
          </div>
        </div>
      ) : (
        <div className="ceo__composer">
          <textarea
            ref={inputRef}
            className="ceo__input"
            value={text}
            placeholder={placeholder}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
          />
          <div className="ceo__composer-row">
            <label className="ceo__toggle">
              <input
                type="checkbox"
                checked={quick}
                disabled={ch.canReply}
                onChange={(e) => setQuick(e.target.checked)}
              />
              Quick directive (/directive, no session)
            </label>
            <button
              type="button"
              className="btn btn--primary"
              disabled={ch.busy || text.trim().length === 0}
              onClick={() => void submit()}
            >
              {ch.busy ? 'Sending…' : ch.canReply ? 'Reply' : 'Send'}
            </button>
          </div>
        </div>
      )}

      {terminal && ch.phase === 'terminal-ok' && (
        <div className="ceo__done">Done. Start a new thread to steer again.</div>
      )}
    </section>
  );
}
