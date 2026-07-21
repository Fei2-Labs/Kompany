// Runtime control state for the board header. Reads GET /runtime, exposes
// suspend/resume/heartbeat actions over the verified engine routes, and
// refetches live when an `audit.runtime.*` SSE arrives (or after an action).

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ApiError,
  getRuntime,
  resumeRuntime,
  runHeartbeat,
  suspendRuntime,
} from '../api/client';
import { events } from '../api/events';
import type { RuntimeState } from '../api/types';

export type RuntimeLoad = 'loading' | 'ready' | 'error';

function errMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.status === 0
      ? 'Engine unreachable'
      : `Request failed (${err.status})`;
  }
  return err instanceof Error ? err.message : 'Unknown error';
}

export interface RuntimeController {
  runtime: RuntimeState | null;
  state: RuntimeLoad;
  error: string | null;
  /** An action (suspend/resume/heartbeat) is mid-flight. */
  busy: boolean;
  /** Transient note from the last heartbeat (cleared on next action). */
  note: string | null;
  suspend: (reason: string) => Promise<void>;
  resume: () => Promise<void>;
  heartbeat: () => Promise<void>;
}

export function useRuntime(): RuntimeController {
  const [runtime, setRuntime] = useState<RuntimeState | null>(null);
  const [state, setState] = useState<RuntimeLoad>('loading');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const refetch = useCallback(async () => {
    try {
      const rt = await getRuntime();
      if (!aliveRef.current) return;
      setRuntime(rt);
      setState('ready');
      setError(null);
    } catch (err) {
      if (!aliveRef.current) return;
      setState('error');
      setError(errMessage(err));
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  // Live: the engine emits audit.runtime.suspended / .resumed.
  useEffect(() => {
    return events.subscribe('runtime', () => {
      void refetch();
    });
  }, [refetch]);

  const suspend = useCallback(
    async (reason: string) => {
      setBusy(true);
      setNote(null);
      try {
        const rt = await suspendRuntime(reason.trim() || 'manual');
        if (aliveRef.current) setRuntime(rt);
      } catch (err) {
        if (aliveRef.current) setError(errMessage(err));
      } finally {
        if (aliveRef.current) setBusy(false);
      }
    },
    [],
  );

  const resume = useCallback(async () => {
    setBusy(true);
    setNote(null);
    try {
      const rt = await resumeRuntime();
      if (aliveRef.current) setRuntime(rt);
    } catch (err) {
      if (aliveRef.current) setError(errMessage(err));
    } finally {
      if (aliveRef.current) setBusy(false);
    }
  }, []);

  const heartbeat = useCallback(async () => {
    setBusy(true);
    setNote(null);
    try {
      const report = await runHeartbeat();
      if (aliveRef.current) {
        const pending = report['pending_approvals'];
        const active = report['active_projects'];
        setNote(
          `Heartbeat ok — ${typeof pending === 'number' ? pending : 0} pending, ` +
            `${typeof active === 'number' ? active : 0} active`,
        );
      }
      void refetch();
    } catch (err) {
      if (aliveRef.current) setError(errMessage(err));
    } finally {
      if (aliveRef.current) setBusy(false);
    }
  }, [refetch]);

  return { runtime, state, error, busy, note, suspend, resume, heartbeat };
}
