// Board data orchestration: fetch the four sources, derive the columns, and
// keep them live via the shared SSE dispatcher. Each source tracks its own
// load/error state so a single failing endpoint degrades one column, not the
// whole board. SSE triggers refetch only the affected source.

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ApiError,
  getEpisodes,
  getInbox,
  getProject,
  getProjects,
  getProjectsIncludingDraft,
  getStatus,
} from '../api/client';
import { events, type LlmSpendData } from '../api/events';
import type {
  ApprovalRequest,
  CompanyStatus,
  EpisodeRow,
  ProjectDetail,
  ProjectListItem,
} from '../api/types';

export type LoadState = 'loading' | 'ready' | 'error';

export interface AsyncSlice<T> {
  data: T;
  state: LoadState;
  error: string | null;
}

function errMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.status === 0
      ? 'Engine unreachable'
      : `Request failed (${err.status})`;
  }
  return err instanceof Error ? err.message : 'Unknown error';
}

interface BoardData {
  /** Active projects (In-Flight source). */
  active: AsyncSlice<ProjectListItem[]>;
  /** include_draft list (Backlog + Done source). */
  withDraft: AsyncSlice<ProjectListItem[]>;
  /** Detail per active project id (In-Flight enrichment + blocked tasks). */
  details: Map<string, ProjectDetail>;
  inbox: AsyncSlice<ApprovalRequest[]>;
  episodes: AsyncSlice<EpisodeRow[]>;
  status: AsyncSlice<CompanyStatus | null>;
  /** Live AI-cost delta accumulated from `llm.spend` since the last refetch. */
  spendDelta: number;
  /** Imperative inbox refetch — used after an approval action resolves. */
  refetchInbox: () => Promise<void>;
  /** Optimistically drop an approval id from the inbox before its refetch lands. */
  dropInbox: (id: string) => void;
}

/**
 * Drives the whole board. Returns per-source slices + a derived detail map.
 * Components compose columns from these via the pure helpers in classify.ts.
 */
export function useBoardData(): BoardData {
  const [active, setActive] = useState<AsyncSlice<ProjectListItem[]>>({
    data: [],
    state: 'loading',
    error: null,
  });
  const [withDraft, setWithDraft] = useState<AsyncSlice<ProjectListItem[]>>({
    data: [],
    state: 'loading',
    error: null,
  });
  const [details, setDetails] = useState<Map<string, ProjectDetail>>(new Map());
  const [inbox, setInbox] = useState<AsyncSlice<ApprovalRequest[]>>({
    data: [],
    state: 'loading',
    error: null,
  });
  const [episodes, setEpisodes] = useState<AsyncSlice<EpisodeRow[]>>({
    data: [],
    state: 'loading',
    error: null,
  });
  const [status, setStatus] = useState<AsyncSlice<CompanyStatus | null>>({
    data: null,
    state: 'loading',
    error: null,
  });
  const [spendDelta, setSpendDelta] = useState(0);

  // Guard against setState after unmount across the async fetch chains.
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const refetchProjects = useCallback(async () => {
    try {
      const list = await getProjects();
      if (!aliveRef.current) return;
      setActive({ data: list, state: 'ready', error: null });
      // Enrich each active project with its detail (task counts + blocked
      // tasks). Best-effort per project; one failure doesn't sink the rest.
      const entries = await Promise.allSettled(
        list.map((p) => getProject(p.id)),
      );
      if (!aliveRef.current) return;
      const map = new Map<string, ProjectDetail>();
      for (const e of entries) {
        if (e.status === 'fulfilled') map.set(e.value.id, e.value);
      }
      setDetails(map);
    } catch (err) {
      if (!aliveRef.current) return;
      setActive((s) => ({ ...s, state: 'error', error: errMessage(err) }));
    }
  }, []);

  const refetchWithDraft = useCallback(async () => {
    try {
      const list = await getProjectsIncludingDraft();
      if (!aliveRef.current) return;
      setWithDraft({ data: list, state: 'ready', error: null });
    } catch (err) {
      if (!aliveRef.current) return;
      setWithDraft((s) => ({ ...s, state: 'error', error: errMessage(err) }));
    }
  }, []);

  const refetchInbox = useCallback(async () => {
    try {
      const list = await getInbox();
      if (!aliveRef.current) return;
      setInbox({ data: list, state: 'ready', error: null });
    } catch (err) {
      if (!aliveRef.current) return;
      setInbox((s) => ({ ...s, state: 'error', error: errMessage(err) }));
    }
  }, []);

  // Optimistic removal: drop a just-resolved approval from the inbox slice so
  // the Needs-You column updates immediately, before the authoritative refetch
  // (triggered by the action + the audit.approval.* SSE) reconciles it.
  const dropInbox = useCallback((id: string) => {
    setInbox((s) => ({ ...s, data: s.data.filter((a) => a.id !== id) }));
  }, []);

  const refetchEpisodes = useCallback(async () => {
    try {
      const list = await getEpisodes();
      if (!aliveRef.current) return;
      setEpisodes({ data: list, state: 'ready', error: null });
    } catch (err) {
      if (!aliveRef.current) return;
      setEpisodes((s) => ({ ...s, state: 'error', error: errMessage(err) }));
    }
  }, []);

  const refetchStatus = useCallback(async () => {
    try {
      const s = await getStatus();
      if (!aliveRef.current) return;
      setStatus({ data: s, state: 'ready', error: null });
      setSpendDelta(0); // reconciled by the fresh cumulative total
    } catch (err) {
      if (!aliveRef.current) return;
      setStatus((st) => ({ ...st, state: 'error', error: errMessage(err) }));
    }
  }, []);

  // Initial load.
  useEffect(() => {
    void refetchProjects();
    void refetchWithDraft();
    void refetchInbox();
    void refetchEpisodes();
    void refetchStatus();
  }, [refetchProjects, refetchWithDraft, refetchInbox, refetchEpisodes, refetchStatus]);

  // Live updates: subscribe to the shared dispatcher's refetch triggers.
  useEffect(() => {
    const unsubInbox = events.subscribe('inbox', () => {
      void refetchInbox();
    });
    const unsubProjects = events.subscribe('projects', () => {
      void refetchProjects();
      void refetchWithDraft();
      void refetchStatus();
    });
    const unsubEpisodes = events.subscribe('episodes', () => {
      void refetchEpisodes();
      void refetchWithDraft();
    });
    const unsubSpend = events.subscribe('spend', (ev) => {
      const cost = (ev.data as LlmSpendData).cost_usd;
      if (typeof cost === 'number') setSpendDelta((d) => d + cost);
    });
    return () => {
      unsubInbox();
      unsubProjects();
      unsubEpisodes();
      unsubSpend();
    };
  }, [refetchInbox, refetchProjects, refetchWithDraft, refetchEpisodes, refetchStatus]);

  return {
    active,
    withDraft,
    details,
    inbox,
    episodes,
    status,
    spendDelta,
    refetchInbox,
    dropInbox,
  };
}
