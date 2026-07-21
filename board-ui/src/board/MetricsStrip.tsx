// Top-of-board metrics strip from GET /status (LLM-free). AI cost live-bumps
// with the accumulated `llm.spend` delta until the next /status refetch
// reconciles the cumulative total. Runway is labelled virtual days, NOT
// wall-clock (model D).

import type { AsyncSlice } from './useBoardData';
import type { CompanyStatus } from '../api/types';
import { money, moneyCents } from './format';

interface MetricsStripProps {
  status: AsyncSlice<CompanyStatus | null>;
  spendDelta: number;
}

interface Metric {
  label: string;
  value: string;
  sub?: string;
}

export function MetricsStrip({ status, spendDelta }: MetricsStripProps) {
  if (status.state === 'error') {
    return (
      <div className="strip strip--error">
        {status.error ?? 'Status unavailable.'}
      </div>
    );
  }
  if (status.state === 'loading' || !status.data) {
    return (
      <div className="strip">
        {['Cash', 'Runway', 'AI cost', 'Active projects'].map((label) => (
          <div className="metric metric--loading" key={label}>
            <span className="metric__label">{label}</span>
            <span className="metric__value">…</span>
          </div>
        ))}
      </div>
    );
  }

  const s = status.data;
  const metrics: Metric[] = [
    { label: 'Cash', value: money(s.balance) },
    {
      label: 'Runway',
      value: `${s.virtual_days_remaining} virtual days`,
      sub: `of ${s.virtual_days_budget} budgeted`,
    },
    {
      label: 'AI cost',
      value: moneyCents(s.total_ai_costs + spendDelta),
      sub: spendDelta > 0 ? `+${moneyCents(spendDelta)} live` : undefined,
    },
    { label: 'Active projects', value: String(s.active_projects) },
  ];

  return (
    <div className="strip">
      {metrics.map((m) => (
        <div className="metric" key={m.label}>
          <span className="metric__label">{m.label}</span>
          <span className="metric__value">{m.value}</span>
          {m.sub && <span className="metric__sub">{m.sub}</span>}
        </div>
      ))}
    </div>
  );
}
