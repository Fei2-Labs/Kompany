// Header runtime/control strip: shows engine state (running | suspended) and
// lets the founder Suspend / Resume / Heartbeat the whole engine. Suspend
// reason is an inline field — NO native dialog (Tauri WebView). State reflects
// live via the `audit.runtime.*` SSE trigger (see useRuntime).

import { useState } from 'react';
import { useRuntime } from './useRuntime';

export function RuntimeStrip() {
  const rt = useRuntime();
  const [suspendOpen, setSuspendOpen] = useState(false);
  const [reason, setReason] = useState('');

  const suspended = rt.runtime?.state === 'suspended';
  const label =
    rt.state === 'loading'
      ? 'checking…'
      : rt.state === 'error'
        ? 'unknown'
        : (rt.runtime?.state ?? 'unknown');

  const confirmSuspend = async () => {
    await rt.suspend(reason);
    setReason('');
    setSuspendOpen(false);
  };

  return (
    <div className="runtimebar">
      <span
        className={`runtimebar__dot runtimebar__dot--${
          suspended ? 'suspended' : rt.state === 'ready' ? 'running' : 'unknown'
        }`}
        aria-hidden
      />
      <span className="runtimebar__label">Engine</span>
      <span className="runtimebar__state">{label}</span>
      {suspended && rt.runtime?.reason && (
        <span className="runtimebar__reason">· {rt.runtime.reason}</span>
      )}

      <div className="runtimebar__actions">
        {suspended ? (
          <button
            type="button"
            className="btn btn--primary btn--sm"
            disabled={rt.busy || rt.state !== 'ready'}
            onClick={() => void rt.resume()}
          >
            Resume
          </button>
        ) : suspendOpen ? (
          <span className="runtimebar__suspendform">
            <input
              className="runtimebar__reasoninput"
              placeholder="Reason (optional)"
              aria-label="Suspend reason"
              value={reason}
              autoFocus
              onChange={(e) => setReason(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void confirmSuspend();
                if (e.key === 'Escape') setSuspendOpen(false);
              }}
            />
            <button
              type="button"
              className="btn btn--danger btn--sm"
              disabled={rt.busy}
              onClick={() => void confirmSuspend()}
            >
              Suspend
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setSuspendOpen(false)}
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={rt.busy || rt.state !== 'ready'}
            onClick={() => setSuspendOpen(true)}
          >
            Suspend
          </button>
        )}
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          disabled={rt.busy}
          onClick={() => void rt.heartbeat()}
        >
          Heartbeat
        </button>
      </div>

      {rt.note && <span className="runtimebar__note">{rt.note}</span>}
      {rt.error && rt.state === 'error' && (
        <span className="runtimebar__err">{rt.error}</span>
      )}
    </div>
  );
}
