// Studio — agent-first workspace (09-08-studio-ui, PR1: team rail + live
// session stream + company strip; right rail holds a NEEDS YOU summary until
// PR2/PR3 land the full cards and artifacts).

import { useEffect, useMemo, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { getAgents, getInbox } from '../api/client';
import type { AgentStatus, ApprovalRequest } from '../api/types';
import { useAsync } from '../panes/useAsync';
import { classifyAction } from '../board/classify';
import { SessionPane } from './SessionPane';
import { TeamRail } from './TeamRail';
import { useActivity } from './useActivity';
import './skins.css';
import './studio.css';

const inboxLoader = (signal?: AbortSignal) => getInbox(signal);
const agentsLoader = (signal?: AbortSignal) => getAgents(signal);

export function Studio() {
  const [selected, setSelected] = useState<string | null>(null);
  const { linesFor, backfill, version } = useActivity();
  const inbox = useAsync<ApprovalRequest[]>(inboxLoader, ['inbox']);
  const agents = useAsync<AgentStatus[]>(agentsLoader, ['projects', 'runtime']);

  useEffect(() => {
    if (selected) void backfill(selected);
  }, [selected, backfill]);

  // version changes whenever a buffer changes → recompute the visible slice
  const lines = useMemo(() => linesFor(selected ?? 'company'), [linesFor, selected, version]); // eslint-disable-line react-hooks/exhaustive-deps
  const task = agents.data?.find((a) => a.role.toLowerCase() === selected)?.last_action ?? null;
  const pending = (inbox.data ?? []).filter((r) => r.status === 'pending' || r.status === 'snoozed');

  return (
    <div className="studio" data-testid="studio">
      <TeamRail selected={selected} onSelect={setSelected} />
      <SessionPane role={selected} lines={lines} task={selected ? task : null} />
      <section className="studio__col studio__right" aria-label="Needs you">
        <header className="studio__head">needs you <b>{pending.length}</b></header>
        <div className="studio__scroll">
          {pending.slice(0, 8).map((r) => {
            const cls = classifyAction(r.action_type);
            return (
              <div className="rail__card" key={r.id}>
                <span className={`rail__tag rail__tag--${cls}`}>{cls === 'ship-gate' ? 'decision' : cls}</span>
                <div>{r.summary}</div>
              </div>
            );
          })}
          {pending.length === 0 && inbox.state === 'ready' && <div className="rail__hint">nothing needs you</div>}
          {pending.length > 0 && (
            <div className="rail__hint">
              <NavLink className="rail__link" to="/board">open the full cards →</NavLink> (approve / revise / snooze land here in the next stage)
            </div>
          )}
          <header className="studio__head">artifacts</header>
          <div className="rail__hint">coming next stage — produced files and documents of the selected agent</div>
        </div>
      </section>
    </div>
  );
}
