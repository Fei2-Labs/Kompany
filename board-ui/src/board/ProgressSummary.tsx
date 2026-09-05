import type { CompanyStatus, ProjectListItem } from '../api/types';

interface ProgressSummaryProps {
  status: CompanyStatus | null;
  activeProjects: ProjectListItem[];
  needsYouCount: number;
}

function nextCheck(ticker: CompanyStatus['ticker'] | undefined): string {
  if (!ticker?.interval_seconds) return 'Waiting for engine status';
  if (!ticker.last_tick_at) return `Every ${ticker.interval_seconds}s`;

  const next = new Date(ticker.last_tick_at).getTime() + ticker.interval_seconds * 1000;
  const seconds = Math.max(0, Math.round((next - Date.now()) / 1000));
  if (seconds < 60) return `In ${seconds}s`;
  return `In ${Math.ceil(seconds / 60)}m`;
}

export function ProgressSummary({
  status,
  activeProjects,
  needsYouCount,
}: ProgressSummaryProps) {
  const running = status?.ticker.running ?? false;
  const activeLabel = activeProjects.length === 1 ? 'project' : 'projects';
  const attentionLabel = needsYouCount === 1 ? 'item' : 'items';

  return (
    <section className="progress-summary" aria-label="Progress summary">
      <div className="progress-summary__intro">
        <span className={`progress-summary__signal progress-summary__signal--${running ? 'ok' : 'idle'}`} />
        <div>
          <p className="progress-summary__eyebrow">Company progress</p>
          <h2 className="progress-summary__title">
            {running ? 'Kompany is working' : 'Kompany is standing by'}
          </h2>
          <p className="progress-summary__hint">
            {needsYouCount > 0
              ? `${needsYouCount} ${attentionLabel} needs your attention.`
              : activeProjects.length > 0
                ? `${activeProjects.length} active ${activeLabel}. Nothing needs your attention.`
                : 'No active work yet.'}
          </p>
        </div>
      </div>

      <div className="progress-summary__facts">
        <div className="progress-summary__fact">
          <span className="progress-summary__label">Current work</span>
          <strong>{activeProjects.length || '—'}</strong>
          <span>{activeProjects.length ? activeLabel : 'none'}</span>
        </div>
        <div className={`progress-summary__fact${needsYouCount ? ' progress-summary__fact--attention' : ''}`}>
          <span className="progress-summary__label">Needs you</span>
          <strong>{needsYouCount}</strong>
          <span>{attentionLabel}</span>
        </div>
        <div className="progress-summary__fact">
          <span className="progress-summary__label">Next check</span>
          <strong>{nextCheck(status?.ticker)}</strong>
          <span>{status?.ticker?.tick_count ?? 0} checks completed</span>
        </div>
      </div>
    </section>
  );
}
