// Studio — agent-first workspace (09-08-studio-ui). PR1: team rail + live
// session stream + company strip. PR2: NEEDS YOU cards with actions in the
// right rail (money / connect / decision). PR3 lands artifacts.

import { useEffect, useMemo, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { getAgents } from '../api/client';
import type { AgentStatus } from '../api/types';
import type { UseChannel } from '../channel/useChannel';
import { useAsync } from '../panes/useAsync';
import { NeedsYouCard } from './NeedsYouCard';
import { SessionPane } from './SessionPane';
import { TeamRail } from './TeamRail';
import { useActivity } from './useActivity';
import { useNeedsYou } from './useNeedsYou';
import './skins.css';
import './studio.css';

const agentsLoader = (signal?: AbortSignal) => getAgents(signal);

export function Studio({ channel }: { channel?: UseChannel }) {
  const [selected, setSelected] = useState<string | null>(null);
  const { linesFor, backfill, version } = useActivity();
  const ny = useNeedsYou();
  const agents = useAsync<AgentStatus[]>(agentsLoader, ['projects', 'runtime']);

  useEffect(() => {
    if (selected) void backfill(selected);
  }, [selected, backfill]);

  // version changes whenever a buffer changes → recompute the visible slice
  const lines = useMemo(() => linesFor(selected ?? 'company'), [linesFor, selected, version]); // eslint-disable-line react-hooks/exhaustive-deps
  const task = agents.data?.find((a) => a.role.toLowerCase() === selected)?.last_action ?? null;

  return (
    <div className="studio" data-testid="studio">
      <TeamRail selected={selected} onSelect={setSelected} />
      <SessionPane role={selected} lines={lines} task={selected ? task : null} />
      <section className="studio__col studio__right" aria-label="Needs you">
        <header className="studio__head">
          needs you <b>{ny.items.length}</b>
        </header>
        <div className="studio__scroll">
          {ny.items.slice(0, 8).map((i) => (
            <NeedsYouCard key={i.id} item={i} channel={channel} onResolved={ny.resolved} compact />
          ))}
          {ny.items.length === 0 && ny.state === 'ready' && <div className="rail__hint">nothing needs you</div>}
          {ny.items.length > 8 && (
            <div className="rail__hint">
              <NavLink className="rail__link" to="/needs-you">all {ny.items.length} →</NavLink>
            </div>
          )}
          <header className="studio__head">artifacts</header>
          <div className="rail__hint">coming next stage — produced files and documents of the selected agent</div>
        </div>
      </section>
    </div>
  );
}
