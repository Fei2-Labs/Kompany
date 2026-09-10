// Per-role live buffers fed by the app-wide SSE dispatcher, with server replay
// on first open of a role. One hook instance per Studio mount.

import { useCallback, useEffect, useRef, useState } from 'react';
import { events } from '../api/events';
import { lineForEvent, lineFromReplay, mergeBackfill, pushLine, roleOf, type Line } from './activity';

interface ReplayResponse { lines: Record<string, unknown>[] }

export function useActivity() {
  const buffers = useRef<Map<string, Line[]>>(new Map());
  const backfilled = useRef<Set<string>>(new Set());
  const [version, setVersion] = useState(0);
  const bump = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    const unsub = events.subscribeAll((ev) => {
      const line = lineForEvent(ev);
      if (!line) return;
      const role = roleOf(ev) ?? 'company';
      const cur = buffers.current.get(role) ?? [];
      buffers.current.set(role, pushLine(cur, line));
      if (role !== 'company') {
        const all = buffers.current.get('company') ?? [];
        buffers.current.set('company', pushLine(all, { ...line, detail: `${role}${line.detail ? ' · ' + line.detail : ''}` }));
      }
      bump();
    });
    return unsub;
  }, [bump]);

  const backfill = useCallback(async (role: string) => {
    if (backfilled.current.has(role)) return;
    backfilled.current.add(role);
    try {
      const res = await fetch(`/activity/${encodeURIComponent(role)}?limit=200`, { headers: { accept: 'application/json' } });
      if (!res.ok) return;
      const body = (await res.json()) as ReplayResponse;
      const replay = (body.lines ?? []).map(lineFromReplay);
      buffers.current.set(role, mergeBackfill(replay, buffers.current.get(role) ?? []));
      bump();
    } catch {
      backfilled.current.delete(role); // retry on next open
    }
  }, [bump]);

  const linesFor = useCallback((role: string): Line[] => buffers.current.get(role) ?? [], []);
  return { linesFor, backfill, version };
}
