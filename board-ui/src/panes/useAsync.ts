// Tiny read-only async loader for the placeholder panes (Agents / Usage /
// Projects / Autopilot). One fetch on mount, optional SSE-triggered refetch,
// consistent loading/empty/error states. NOT a data-writing surface — these
// panes are read-only by design (autonomy model: no manual assignment).

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { events, type RefetchTrigger } from '../api/events';

export type AsyncState = 'loading' | 'ready' | 'error';

export interface AsyncResult<T> {
  data: T | null;
  state: AsyncState;
  error: string | null;
  /** Imperative refetch — used by write surfaces (Settings) after a successful
   *  mutation, so the displayed status/mask updates without waiting for an SSE. */
  reload: () => void;
}

interface AsyncStateValue<T> {
  data: T | null;
  state: AsyncState;
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

/**
 * Fetch `loader` once on mount; refetch when any of `refetchOn` SSE triggers
 * fire. `loader` must be stable (wrap in useCallback or define at module scope).
 */
export function useAsync<T>(
  loader: (signal?: AbortSignal) => Promise<T>,
  refetchOn: RefetchTrigger[] = [],
): AsyncResult<T> {
  const [result, setResult] = useState<AsyncStateValue<T>>({
    data: null,
    state: 'loading',
    error: null,
  });

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const data = await loader();
      if (!aliveRef.current) return;
      setResult({ data, state: 'ready', error: null });
    } catch (err) {
      if (!aliveRef.current) return;
      setResult((r) => ({ ...r, state: 'error', error: errMessage(err) }));
    }
  }, [loader]);

  useEffect(() => {
    void load();
  }, [load]);

  // Stable join key so the effect doesn't re-subscribe on identical arrays.
  const triggerKey = refetchOn.join(',');
  useEffect(() => {
    if (!triggerKey) return;
    const triggers = triggerKey.split(',') as RefetchTrigger[];
    const unsubs = triggers.map((t) => events.subscribe(t, () => void load()));
    return () => unsubs.forEach((u) => u());
  }, [triggerKey, load]);

  return { ...result, reload: () => void load() };
}
