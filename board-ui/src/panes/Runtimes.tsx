// Runtimes pane — the full engine-control surface. Mirrors the compact header
// strip but with the persisted state detail (reason, since). Suspend/resume/
// heartbeat over the verified /runtime + /heartbeat routes. No native dialogs.

import { useState } from 'react';
import { useRuntime } from '../runtime/useRuntime';

export function Runtimes() {
  const rt = useRuntime();
  const [reason, setReason] = useState('');
  const suspended = rt.runtime?.state === 'suspended';

  return (
    <section className="pane">
      <header className="pane__header">
        <h1 className="pane__title">Runtimes</h1>
        <p className="pane__subtitle">
          Pause or resume the whole engine. Suspending stops new autonomous work;
          resuming lets the daemon and agents continue.
        </p>
      </header>

      {rt.state === 'loading' ? (
        <div className="pane__empty">Loading…</div>
      ) : rt.state === 'error' ? (
        <div className="pane__empty pane__empty--error">{rt.error}</div>
      ) : (
        <div className="runtimepane">
          <div className="runtimepane__state">
            <span
              className={`runtimebar__dot runtimebar__dot--${suspended ? 'suspended' : 'running'}`}
              aria-hidden
            />
            <span className="runtimepane__statelabel">
              {rt.runtime?.state ?? 'unknown'}
            </span>
            {rt.runtime?.reason && (
              <span className="runtimepane__reason">{rt.runtime.reason}</span>
            )}
            {rt.runtime?.since && (
              <span className="runtimepane__since">since {rt.runtime.since}</span>
            )}
          </div>

          <div className="runtimepane__controls">
            {suspended ? (
              <button
                type="button"
                className="btn btn--primary"
                disabled={rt.busy}
                onClick={() => void rt.resume()}
              >
                Resume engine
              </button>
            ) : (
              <span className="runtimepane__suspend">
                <input
                  className="runtimebar__reasoninput"
                  placeholder="Suspend reason (optional)"
                  aria-label="Suspend reason"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
                <button
                  type="button"
                  className="btn btn--danger"
                  disabled={rt.busy}
                  onClick={() => void rt.suspend(reason).then(() => setReason(''))}
                >
                  Suspend engine
                </button>
              </span>
            )}
            <button
              type="button"
              className="btn btn--ghost"
              disabled={rt.busy}
              onClick={() => void rt.heartbeat()}
            >
              Run heartbeat
            </button>
          </div>

          {rt.note && <p className="runtimepane__note">{rt.note}</p>}
        </div>
      )}
    </section>
  );
}
