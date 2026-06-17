// Persistent bottom command bar: a one-line `kompany>` input wired to the shared
// CEO channel. Sends open/continue a session; if the CEO clarifies or gates, the
// bar shows a compact status + a "expand" link to the full Talk-to-CEO pane.
// Inline only — no native dialogs (Tauri WebView).

import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { moneyCents } from '../board/format';
import { errorLabel } from './state';
import type { UseChannel } from './useChannel';

interface CommandBarProps {
  channel: UseChannel;
  /** Bumped by the palette "send to CEO" action to focus the bar. */
  focusSignal: number;
}

export function CommandBar({ channel: ch, focusSignal }: CommandBarProps) {
  const [text, setText] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (focusSignal > 0) inputRef.current?.focus();
  }, [focusSignal]);

  const submit = async () => {
    const t = text.trim();
    if (!t || ch.busy) return;
    setText('');
    await ch.send(t);
  };

  const lastCeo = [...ch.turns].reverse().find((tr) => tr.role === 'ceo');

  return (
    <div className="cmdbar">
      {(ch.phase === 'clarify' || ch.canGo || ch.phase === 'terminal-failed') && lastCeo && (
        <div className="cmdbar__status">
          <span className="cmdbar__status-text">
            {ch.phase === 'terminal-failed' && ch.last?.error_code
              ? errorLabel(ch.last.error_code)
              : lastCeo.text}
          </span>
          {ch.canGo ? (
            <span className="cmdbar__gate">
              <button
                type="button"
                className="btn btn--primary btn--sm"
                disabled={ch.busy}
                onClick={() => void ch.go()}
              >
                GO
              </button>
              <button
                type="button"
                className="btn btn--sm"
                disabled={ch.busy}
                onClick={() => void ch.abandon()}
              >
                Abandon
              </button>
            </span>
          ) : (
            <button
              type="button"
              className="cmdbar__expand"
              onClick={() => navigate('/talk')}
            >
              Open thread
            </button>
          )}
        </div>
      )}
      <div className="cmdbar__row">
        <span className="cmdbar__prompt">kompany&gt;</span>
        <input
          ref={inputRef}
          className="cmdbar__input"
          value={text}
          placeholder={ch.canReply ? 'Reply to the CEO…' : 'State intent for the CEO…'}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              void submit();
            }
          }}
        />
        {ch.totalCost > 0 && (
          <span className="cmdbar__cost">{moneyCents(ch.totalCost)}</span>
        )}
        <button
          type="button"
          className="btn btn--primary btn--sm"
          disabled={ch.busy || text.trim().length === 0}
          onClick={() => void submit()}
        >
          {ch.busy ? '…' : 'Send'}
        </button>
      </div>
    </div>
  );
}
