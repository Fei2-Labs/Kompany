// Usage pane — read-only cost view. Cumulative AI spend from
// GET /llm/spend/summary, joined with the finance figures on GET /status.
// Live-bumps the AI cost on every `llm.spend` SSE, reconciles on refetch.

import { useCallback, useEffect, useState } from 'react';
import { getSpendSummary, getStatus } from '../api/client';
import type { CompanyStatus, LlmSpendSummary } from '../api/types';
import { events, type LlmSpendData } from '../api/events';
import { useAsync } from './useAsync';
import { PaneShell } from './PaneShell';
import { money, moneyCents } from '../board/format';

const spendLoader = (signal?: AbortSignal) => getSpendSummary(signal);
const statusLoader = (signal?: AbortSignal) => getStatus(signal);

export function Usage() {
  const spend = useAsync<LlmSpendSummary>(spendLoader, ['spend']);
  const status = useAsync<CompanyStatus>(statusLoader, ['spend', 'projects']);
  const [delta, setDelta] = useState(0);

  // Live increment between refetches; the 'spend' refetch above zeroes it by
  // re-deriving the authoritative total, so reset whenever a fresh load lands.
  const onSpend = useCallback((ev: { data: Record<string, unknown> }) => {
    const cost = (ev.data as LlmSpendData).cost_usd;
    if (typeof cost === 'number') setDelta((d) => d + cost);
  }, []);
  useEffect(() => events.subscribe('spend', onSpend), [onSpend]);
  useEffect(() => {
    if (spend.state === 'ready') setDelta(0);
  }, [spend.data, spend.state]);

  const ready = spend.state === 'ready' || status.state === 'ready';
  const aiCost = (spend.data?.total_usd ?? 0) + delta;

  return (
    <PaneShell
      title="Usage"
      subtitle="LLM spend & finance — read-only."
      state={ready ? 'ready' : spend.state === 'error' && status.state === 'error' ? 'error' : 'loading'}
      error={spend.error ?? status.error}
    >
      <div className="usage">
        <div className="usage__card">
          <span className="usage__label">AI cost (cumulative)</span>
          <span className="usage__value">{moneyCents(aiCost)}</span>
          <span className="usage__sub">
            {spend.data?.row_count ?? 0} ledger rows
            {delta > 0 ? ` · +${moneyCents(delta)} live` : ''}
          </span>
        </div>
        <div className="usage__card">
          <span className="usage__label">Cash balance</span>
          <span className="usage__value">{money(status.data?.balance ?? 0)}</span>
        </div>
        <div className="usage__card">
          <span className="usage__label">Total expenses</span>
          <span className="usage__value">{money(status.data?.total_expenses ?? 0)}</span>
        </div>
        <div className="usage__card">
          <span className="usage__label">Total income</span>
          <span className="usage__value">{money(status.data?.total_income ?? 0)}</span>
        </div>
      </div>
    </PaneShell>
  );
}
