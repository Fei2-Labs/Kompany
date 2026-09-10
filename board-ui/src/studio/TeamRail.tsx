import { useCallback } from 'react';
import { getAgents, getRuntime, getStatus, resumeRuntime, suspendRuntime } from '../api/client';
import type { AgentStatus, CompanyStatus, RuntimeState } from '../api/types';
import { useAsync } from '../panes/useAsync';

interface Props {
  selected: string | null;
  onSelect: (role: string | null) => void;
}

const agentsLoader = (signal?: AbortSignal) => getAgents(signal);
const statusLoader = (signal?: AbortSignal) => getStatus(signal);
const runtimeLoader = (signal?: AbortSignal) => getRuntime(signal);

function money(n: number): string {
  const s = `$${Math.abs(n).toFixed(2)}`;
  return n < 0 ? `−${s}` : s;
}

export function TeamRail({ selected, onSelect }: Props) {
  const agents = useAsync<AgentStatus[]>(agentsLoader, ['projects', 'runtime']);
  const status = useAsync<CompanyStatus>(statusLoader, ['spend', 'runtime', 'inbox']);
  const runtime = useAsync<RuntimeState>(runtimeLoader, ['runtime']);
  const rtState = runtime.data?.state ?? 'running';
  const working = (agents.data ?? []).filter((a) => a.status !== 'idle').length;

  const toggleRuntime = useCallback(async () => {
    try {
      if (rtState === 'suspended') await resumeRuntime(); else await suspendRuntime('paused from Studio');
    } finally { runtime.reload(); status.reload(); }
  }, [rtState, runtime, status]);

  return (
    <section className="studio__col studio__team" aria-label="Team">
      <header className="studio__head">team <b>{working} working</b></header>
      <div className="studio__scroll">
        <button type="button" className={`team__row ${selected === null ? 'team__row--sel' : ''}`} onClick={() => onSelect(null)}>
          <span className="team__dot team__dot--on" /><span className="team__role">company</span><span className="team__st">all</span>
        </button>
        {(agents.data ?? []).map((a) => {
          const role = a.role.toLowerCase();
          const busy = a.status !== 'idle';
          return (
            <button key={role} type="button" className={`team__row ${selected === role ? 'team__row--sel' : ''}`} onClick={() => onSelect(role)}>
              <span className={`team__dot ${busy ? 'team__dot--on' : ''}`} />
              <span>
                <span className="team__role">{role}</span>
                {a.last_action && busy ? <span className="team__task">{a.last_action}</span> : null}
              </span>
              <span className="team__st">{a.status}</span>
            </button>
          );
        })}
        {agents.state === 'error' && <div className="rail__hint">{agents.error}</div>}
      </div>
      <div className="company">
        {status.data ? (
          <>
            <div className="row"><span>cash</span><b className={status.data.balance < 0 ? 'neg' : ''}>{money(status.data.balance)}</b></div>
            <div className="row"><span>runway</span><b>{status.data.virtual_days_remaining} / {status.data.virtual_days_budget} vd</b></div>
            <div className="row"><span>ai cost</span><b>{money(status.data.total_ai_costs)}</b></div>
            <div className="row"><span>engine</span><b>{rtState}</b></div>
            <div className="company__actions">
              <button className="btn btn--sm" type="button" onClick={() => void toggleRuntime()}>
                {rtState === 'suspended' ? 'Resume' : 'Suspend'}
              </button>
            </div>
          </>
        ) : <span>{status.state === 'error' ? status.error : 'loading…'}</span>}
      </div>
    </section>
  );
}
