// Reports pane — read-only founder reports (GET /reports) with one write
// affordance: "Report now" (POST /reports/generate, period=manual,
// deliver=false). Below the report viewer, two compact audit strips: open
// watchdog health events and recent artifact-lane evolution proposals.
// Refetches on each `daemon.tick` SSE, like Autopilot.

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  generateReport,
  getEvolutionProposals,
  getHealthEvents,
  getReports,
} from '../api/client';
import type {
  EvolutionProposal,
  FounderReport,
  HealthEvent,
  ReportData,
  ReportPeriod,
} from '../api/types';
import { events } from '../api/events';
import { useAsync } from './useAsync';
import { PaneShell } from './PaneShell';

const PERIODS: Array<{ id: ReportPeriod; label: string }> = [
  { id: 'daily', label: 'Daily' },
  { id: 'weekly', label: 'Weekly' },
  { id: 'manual', label: 'Manual' },
];

function firstLine(text: string): string {
  const line = (text ?? '').split('\n').find((l) => l.trim().length > 0) ?? '';
  return line.length > 90 ? `${line.slice(0, 90)}…` : line;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function fmtUsd(n: number | null | undefined): string {
  return typeof n === 'number' ? `$${n.toFixed(2)}` : '—';
}

function countMap(m: Record<string, number> | undefined): string {
  if (!m) return '—';
  const entries = Object.entries(m);
  if (entries.length === 0) return '0';
  return entries.map(([k, v]) => `${k} ${v}`).join(' · ');
}

function pct(rate: number | undefined): string {
  return typeof rate === 'number' ? `${Math.round(rate * 100)}%` : '—';
}

function StatStrip({ data }: { data: ReportData }) {
  const tasks = data.tasks ?? {};
  const taskTotal = Object.values(tasks).reduce((a, b) => a + b, 0);
  const fr = data.flip_rates ?? null;
  return (
    <div className="autopilot__strip reports__strip">
      <div className="autopilot__metric">
        <span className="autopilot__label">Tasks</span>
        <span className="autopilot__value">{taskTotal}</span>
        <span className="reports__sub">{countMap(data.tasks)}</span>
      </div>
      <div className="autopilot__metric">
        <span className="autopilot__label">Active projects</span>
        <span className="autopilot__value">{data.active_projects?.length ?? 0}</span>
        <span className="reports__sub">
          {(data.active_projects ?? []).map((p) => p.name).join(', ') || '—'}
        </span>
      </div>
      <div className="autopilot__metric">
        <span className="autopilot__label">Balance</span>
        <span className="autopilot__value">{fmtUsd(data.balance)}</span>
      </div>
      <div className="autopilot__metric">
        <span className="autopilot__label">AI spend (window)</span>
        <span className="autopilot__value">{fmtUsd(data.ai_spend_window)}</span>
      </div>
      <div className="autopilot__metric">
        <span className="autopilot__label">Open health events</span>
        <span className="autopilot__value">{data.health_events_open ?? 0}</span>
      </div>
      <div className="autopilot__metric">
        <span className="autopilot__label">Pending approvals</span>
        <span className="autopilot__value">{data.pending_approvals?.length ?? 0}</span>
      </div>
      <div className="autopilot__metric">
        <span className="autopilot__label">Evolution proposals</span>
        <span className="autopilot__value">
          {Object.values(data.evolution_proposals ?? {}).reduce((a, b) => a + b, 0)}
        </span>
        <span className="reports__sub">{countMap(data.evolution_proposals)}</span>
      </div>
      <div className="autopilot__metric">
        <span className="autopilot__label">Debates / distillations</span>
        <span className="autopilot__value">
          {data.debates ?? 0} / {data.distillations ?? 0}
        </span>
      </div>
      <div className="autopilot__metric">
        <span className="autopilot__label">Flip rate (code / artifact)</span>
        <span className="autopilot__value">
          {fr ? `${pct(fr.code_lane?.rate)} / ${pct(fr.artifact_lane?.rate)}` : '—'}
        </span>
        {fr?.rubber_stamp && (
          <span className="reports__sub reports__sub--warn">rubber stamp</span>
        )}
      </div>
    </div>
  );
}

function DeliveryChip({ delivery }: { delivery: FounderReport['delivery'] }) {
  if (!delivery || delivery.length === 0) {
    return <span className="reports__chip reports__chip--muted">not delivered</span>;
  }
  return (
    <>
      {delivery.map((d, i) => {
        const ok = d.status === 'sent' || d.status === 'delivered' || d.status === 'ok';
        const cls = ok
          ? 'reports__chip reports__chip--ok'
          : d.status === 'failed' || d.error
            ? 'reports__chip reports__chip--fail'
            : 'reports__chip reports__chip--muted';
        return (
          <span className={cls} key={i} title={d.error ?? undefined}>
            {d.adapter}: {d.status}
            {d.error ? ` (${d.error})` : ''}
          </span>
        );
      })}
    </>
  );
}

export function Reports() {
  const [period, setPeriod] = useState<ReportPeriod>('daily');
  const [reports, setReports] = useState<FounderReport[] | null>(null);
  const [reportsError, setReportsError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);

  const [health, setHealth] = useState<HealthEvent[]>([]);
  const [proposals, setProposals] = useState<EvolutionProposal[]>([]);

  const reportsLoader = useCallback(
    (signal?: AbortSignal) => getReports(period, 30, signal),
    [period],
  );
  const list = useAsync<FounderReport[]>(reportsLoader);

  // Mirror the hook result into local state so "Report now" can splice the
  // new row in and select it without waiting for a full reload.
  useEffect(() => {
    if (list.state === 'ready') {
      setReports(list.data ?? []);
      setReportsError(null);
    } else if (list.state === 'error') {
      setReportsError(list.error);
    }
  }, [list.state, list.data, list.error]);

  // Keep a valid selection: default to the newest row of the active period.
  useEffect(() => {
    if (!reports) return;
    if (selectedId && reports.some((r) => r.id === selectedId)) return;
    setSelectedId(reports[0]?.id ?? null);
  }, [reports, selectedId]);

  const refetchAudit = useCallback(async () => {
    // Both are best-effort enrichment — a failure just hides the section.
    try {
      setHealth(await getHealthEvents('open', 50));
    } catch {
      setHealth([]);
    }
    try {
      setProposals(await getEvolutionProposals(20));
    } catch {
      setProposals([]);
    }
  }, []);

  useEffect(() => {
    void refetchAudit();
  }, [refetchAudit]);

  // `list.reload` is recreated every render; hold it in a ref so the SSE
  // subscription is set up once and still calls the latest loader.
  const reloadRef = useRef(list.reload);
  reloadRef.current = list.reload;

  // The daemon tick loop emits `daemon.tick` — refetch every source on each.
  useEffect(() => {
    return events.subscribeAll((ev) => {
      if (ev.type === 'daemon.tick') {
        reloadRef.current();
        void refetchAudit();
      }
    });
  }, [refetchAudit]);

  const onGenerate = useCallback(async () => {
    setBusy(true);
    setGenError(null);
    try {
      const row = await generateReport('manual', false);
      setPeriod('manual');
      setSelectedId(row.id);
      // Optimistically splice so the row shows even before the reload lands.
      setReports((prev) => [row, ...(prev ?? []).filter((r) => r.id !== row.id)]);
    } catch (err) {
      setGenError(err instanceof Error ? err.message : 'Report generation failed');
    } finally {
      setBusy(false);
    }
  }, []);

  const rows = reports ?? [];
  const selected = rows.find((r) => r.id === selectedId) ?? null;
  const shellState = list.state === 'loading' && reports === null ? 'loading' : reportsError ? 'error' : 'ready';

  return (
    <PaneShell
      title="Reports"
      subtitle="Founder-facing summaries the engine writes on its own schedule. Read-only, except for an on-demand manual report."
      state={shellState}
      error={reportsError}
    >
      <div className="reports">
        <div className="reports__toolbar">
          <div className="reports__tabs" role="tablist">
            {PERIODS.map((p) => (
              <button
                key={p.id}
                type="button"
                role="tab"
                aria-selected={period === p.id}
                className={`reports__tab${period === p.id ? ' reports__tab--active' : ''}`}
                onClick={() => {
                  setPeriod(p.id);
                  setSelectedId(null);
                }}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="reports__actions">
            {genError && <span className="reports__generr">{genError}</span>}
            <button
              type="button"
              className="btn btn--primary btn--sm"
              disabled={busy}
              onClick={() => void onGenerate()}
            >
              {busy ? 'Generating…' : 'Report now'}
            </button>
          </div>
        </div>

        {rows.length === 0 ? (
          <div className="pane__empty">
            No {period} reports yet.
            {period === 'manual' ? ' Use "Report now" to generate one.' : ''}
          </div>
        ) : (
          <div className="reports__split">
            <ul className="reports__list">
              {rows.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    className={`reports__row${r.id === selectedId ? ' reports__row--active' : ''}`}
                    onClick={() => setSelectedId(r.id)}
                  >
                    <span className="reports__row-date">{fmtDate(r.generated_at)}</span>
                    <span className="reports__row-line">
                      {firstLine(r.narrative) || '(empty narrative)'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>

            <div className="reports__detail">
              {selected ? (
                <>
                  <div className="reports__meta">
                    <span className="reports__meta-period">{selected.period}</span>
                    <span className="reports__meta-range">
                      {fmtDate(selected.period_start)} → {fmtDate(selected.period_end)}
                    </span>
                    <span className="reports__meta-cost">cost {fmtUsd(selected.cost)}</span>
                    <DeliveryChip delivery={selected.delivery} />
                  </div>
                  <StatStrip data={selected.data ?? {}} />
                  <pre className="reports__narrative">{selected.narrative}</pre>
                </>
              ) : (
                <div className="pane__empty">Select a report.</div>
              )}
            </div>
          </div>
        )}

        <h2 className="autopilot__heading reports__heading">Open health events</h2>
        {health.length === 0 ? (
          <div className="pane__empty">No open health events.</div>
        ) : (
          <ul className="ticklist">
            {health.map((h) => (
              <li className="tickrow reports__auditrow" key={h.id}>
                <span className="tickrow__outcome">{h.kind}</span>
                <span className="tickrow__actions">
                  {h.project_id ? `project ${h.project_id}` : ''}
                  {h.project_id && h.task_id ? ' · ' : ''}
                  {h.task_id ? `task ${h.task_id}` : ''}
                  {!h.project_id && !h.task_id ? '—' : ''}
                </span>
                <span className="tickrow__dur">{fmtDate(h.created_at)}</span>
              </li>
            ))}
          </ul>
        )}

        <h2 className="autopilot__heading reports__heading">Recent evolution proposals</h2>
        {proposals.length === 0 ? (
          <div className="pane__empty">No evolution proposals yet.</div>
        ) : (
          <ul className="ticklist">
            {proposals.map((p) => (
              <li className="tickrow reports__auditrow" key={p.id}>
                <span className="tickrow__outcome">
                  {p.status}
                  {p.doctor_status ? ` · doctor ${p.doctor_status}` : ''}
                </span>
                <span className="tickrow__actions" title={p.summary ?? undefined}>
                  {p.kind}: {p.target}
                  {p.summary ? ` — ${p.summary}` : ''}
                </span>
                <span className="tickrow__dur">{fmtDate(p.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </PaneShell>
  );
}
