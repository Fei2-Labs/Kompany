// Agents pane — read-only org view from GET /agents/status (11 C-suite roles).
// NOT a squad editor: no assignment, no role creation. Autonomy model — the
// founder watches the org, the CEO routes the work.

import { getAgents } from '../api/client';
import type { AgentStatus } from '../api/types';
import { useAsync } from './useAsync';
import { PaneShell } from './PaneShell';

const loader = (signal?: AbortSignal) => getAgents(signal);

export function Agents() {
  // Agent activity rides harness.event / audit.* → the 'projects' trigger.
  const res = useAsync<AgentStatus[]>(loader, ['projects']);
  const rows = res.data ?? [];

  return (
    <PaneShell
      title="Agents"
      subtitle="Live C-suite org — read-only. The CEO routes work; you don't assign it."
      state={res.state}
      error={res.error}
      empty={rows.length === 0}
      emptyText="No agent activity yet."
    >
      <ul className="orglist">
        {rows.map((a) => (
          <li className="orgrow" key={a.role}>
            <span className={`orgrow__dot orgrow__dot--${a.status === 'idle' ? 'idle' : 'busy'}`} aria-hidden />
            <span className="orgrow__role">{a.role}</span>
            <span className="orgrow__status">{a.status}</span>
            <span className="orgrow__action">{a.last_action ?? '—'}</span>
          </li>
        ))}
      </ul>
    </PaneShell>
  );
}
