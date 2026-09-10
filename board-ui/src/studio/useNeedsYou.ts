// Data for NEEDS YOU: /inbox + active projects (with task detail) +
// /integrations, kept live by the shared SSE dispatcher. Resolving an
// approval drops it optimistically before the refetch reconciles.

import { useCallback, useMemo } from 'react';
import { getInbox, getIntegrations, getProject, getProjects, type IntegrationInfo } from '../api/client';
import type { ApprovalRequest, ProjectDetail } from '../api/types';
import { useAsync } from '../panes/useAsync';
import { buildNeedsYou, type NeedsItem } from './needsYou';

const inboxLoader = (signal?: AbortSignal) => getInbox(signal);
const integrationsLoader = (signal?: AbortSignal) => getIntegrations(signal);
const detailsLoader = async (signal?: AbortSignal): Promise<ProjectDetail[]> => {
  const list = await getProjects(signal);
  const settled = await Promise.allSettled(list.map((p) => getProject(p.id, signal)));
  return settled.flatMap((s) => (s.status === 'fulfilled' ? [s.value] : []));
};

export interface NeedsYouData {
  items: NeedsItem[];
  state: 'loading' | 'ready' | 'error';
  error: string | null;
  /** An approval was resolved: hide it now, refetch for the truth. */
  resolved: (id: string) => void;
  reload: () => void;
}

export function useNeedsYou(): NeedsYouData {
  const inbox = useAsync<ApprovalRequest[]>(inboxLoader, ['inbox']);
  const details = useAsync<ProjectDetail[]>(detailsLoader, ['projects']);
  const integrations = useAsync<IntegrationInfo[]>(integrationsLoader, ['inbox']);

  const items = useMemo(
    () => buildNeedsYou(inbox.data ?? [], details.data ?? [], integrations.data ?? []),
    [inbox.data, details.data, integrations.data],
  );
  // The inbox is the one source that must load; projects/integrations degrade
  // to "no account cards" rather than blanking the list.
  const state = inbox.state;
  const resolved = useCallback(() => {
    inbox.reload();
  }, [inbox]);
  const reload = useCallback(() => {
    inbox.reload();
    details.reload();
    integrations.reload();
  }, [inbox, details, integrations]);
  return { items, state, error: inbox.error, resolved, reload };
}
