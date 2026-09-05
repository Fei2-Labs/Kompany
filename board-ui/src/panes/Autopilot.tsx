// Autopilot pane — read-only daemon view. Tick-loop liveness from GET /status
// (ticker block) + the recent tick log from GET /observability. Refetches on
// each `daemon.tick` SSE. No controls here — the daemon runs autonomously;
// engine pause/resume lives in the board header runtime strip.

import { useCallback, useEffect, useState } from 'react';
import { getObservability, getStatus } from '../api/client';
import type { CompanyStatus, ObservabilitySnapshot, TickRow } from '../api/types';
import { events } from '../api/events';
import { useAsync } from './useAsync';
import { PaneShell } from './PaneShell';

const statusLoader = (signal?: AbortSignal) => getStatus(signal);

export function Autopilot() {
  const status = useAsync<CompanyStatus>(statusLoader, ['projects']);
  const [obs, setObs] = useState<ObservabilitySnapshot | null>(null);

  const refetchObs = useCallback(async () => {
    try {
      setObs(await getObservability());
    } catch {
      // Observability is best-effort enrichment; the ticker (status) is the
      // authoritative liveness signal, so a failure here just hides the log.
      setObs(null);
    }
  }, []);

  useEffect(() => {
    void refetchObs();
  }, [refetchObs]);

  // The daemon tick loop emits `daemon.tick` — refetch both sources on each.
  useEffect(() => {
    return events.subscribeAll((ev) => {
      if (ev.type === 'daemon.tick') void refetchObs();
    });
  }, [refetchObs]);

  const ticker = status.data?.ticker;
  const ticks: TickRow[] = obs?.recent_ticks ?? [];

  return (
    <PaneShell
      title="Autopilot"
      subtitle="This is the engine's activity log. Return to Board for the plain-language progress summary."
      state={status.state}
      error={status.error}
    >
      <div className="autopilot">
        <div className="autopilot__strip">
          <div className="autopilot__metric">
            <span className="autopilot__label">Loop</span>
            <span className="autopilot__value">
              {ticker?.running ? 'running' : 'idle'}
            </span>
          </div>
          <div className="autopilot__metric">
            <span className="autopilot__label">Ticks</span>
            <span className="autopilot__value">{ticker?.tick_count ?? 0}</span>
          </div>
          <div className="autopilot__metric">
            <span className="autopilot__label">Interval</span>
            <span className="autopilot__value">
              {ticker ? `${ticker.interval_seconds}s` : '—'}
            </span>
          </div>
          <div className="autopilot__metric">
            <span className="autopilot__label">Last tick</span>
            <span className="autopilot__value">{ticker?.last_tick_at ?? '—'}</span>
          </div>
        </div>

        <h2 className="autopilot__heading">Recent ticks</h2>
        {ticks.length === 0 ? (
          <div className="pane__empty">No ticks recorded yet.</div>
        ) : (
          <ul className="ticklist">
            {ticks.map((t, i) => (
              <li className="tickrow" key={i}>
                <span className="tickrow__outcome">{t.outcome ?? 'tick'}</span>
                <span className="tickrow__actions">
                  {(t.actions ?? []).join(', ') || '—'}
                </span>
                <span className="tickrow__dur">
                  {typeof t.duration_ms === 'number' ? `${t.duration_ms}ms` : ''}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </PaneShell>
  );
}
