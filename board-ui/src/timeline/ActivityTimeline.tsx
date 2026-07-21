// Live activity timeline: a unified human + agent feed fed by the shared SSE
// dispatcher (subscribeAll). Renders interleaved audit.* / harness.event /
// llm.spend / daemon.tick / episode.recorded / self_update.event rows. The
// buffer is capped (newest first) so a long-running session can't grow it
// without bound. SSE has no replay, so the feed starts empty on mount.

import { useEffect, useRef, useState } from 'react';
import { events } from '../api/events';
import { rowForEvent, type TimelineRow } from './timeline';

const MAX_ROWS = 200;

interface ActivityTimelineProps {
  /** Compact right-rail mode vs full pane. */
  variant?: 'rail' | 'pane';
}

export function ActivityTimeline({ variant = 'rail' }: ActivityTimelineProps) {
  const [rows, setRows] = useState<TimelineRow[]>([]);
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  useEffect(() => {
    const unsub = events.subscribeAll((ev) => {
      if (pausedRef.current) return;
      const row = rowForEvent(ev);
      if (!row) return;
      setRows((prev) => {
        const next = [row, ...prev];
        return next.length > MAX_ROWS ? next.slice(0, MAX_ROWS) : next;
      });
    });
    return unsub;
  }, []);

  return (
    <aside className={`timeline timeline--${variant}`}>
      <header className="timeline__header">
        <h2 className="timeline__title">Activity</h2>
        <div className="timeline__controls">
          <button
            type="button"
            className="timeline__btn"
            onClick={() => setPaused((p) => !p)}
          >
            {paused ? 'Resume' : 'Pause'}
          </button>
          {rows.length > 0 && (
            <button
              type="button"
              className="timeline__btn"
              onClick={() => setRows([])}
            >
              Clear
            </button>
          )}
        </div>
      </header>
      <div className="timeline__feed">
        {rows.length === 0 ? (
          <p className="timeline__empty">
            Waiting for live activity… (no history replay)
          </p>
        ) : (
          rows.map((r) => (
            <div key={r.id} className={`tl-row tl-row--${r.tone}`}>
              <span className="tl-row__icon">{r.icon}</span>
              <div className="tl-row__main">
                <div className="tl-row__line">
                  <span className="tl-row__actor">{r.actor}</span>
                  <span className="tl-row__summary">{r.summary}</span>
                </div>
                {r.detail && <div className="tl-row__detail">{r.detail}</div>}
              </div>
              <span className="tl-row__time">{fmtTime(r.receivedAt)}</span>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}

function fmtTime(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
