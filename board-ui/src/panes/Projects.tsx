// Projects pane — richer read-only project list (all statuses) from
// GET /projects?include_draft=1. Shows status, type, and funding progress.
// Read-only: the board is where flow happens; this is a flat reference list.

import { getProjectsIncludingDraft } from '../api/client';
import type { ProjectListItem } from '../api/types';
import { useAsync } from './useAsync';
import { PaneShell } from './PaneShell';
import { money, percent, target } from '../board/format';

const loader = (signal?: AbortSignal) => getProjectsIncludingDraft(signal);

function progress(p: ProjectListItem): number | null {
  if (!p.target_amount || p.target_amount <= 0) return null;
  return p.funded_amount / p.target_amount;
}

export function Projects() {
  const res = useAsync<ProjectListItem[]>(loader, ['projects', 'episodes']);
  const rows = res.data ?? [];

  return (
    <PaneShell
      title="Projects"
      subtitle="All projects — read-only. Drag-free: the CEO stages and routes work."
      state={res.state}
      error={res.error}
      empty={rows.length === 0}
      emptyText="No projects yet."
    >
      <ul className="projlist">
        {rows.map((p) => {
          const prog = progress(p);
          return (
            <li className="projrow" key={p.id}>
              <span className="projrow__name">{p.name}</span>
              <span className={`projrow__status projrow__status--${p.status}`}>
                {p.status}
              </span>
              <span className="projrow__type">{p.type}</span>
              <span className="projrow__fund">
                {money(p.funded_amount)} / {target(p.target_amount)}
                {prog !== null ? ` · ${percent(prog)}` : ''}
              </span>
            </li>
          );
        })}
      </ul>
    </PaneShell>
  );
}
