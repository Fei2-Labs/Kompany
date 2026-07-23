// CEO-channel conversation hook: drives a single multi-turn session over
// /channel/send → (clarify reply | GO/Abandon) → terminal. Renders an optimistic
// local turn thread (founder lines appear immediately; CEO replies append from
// the DirectiveResult) and tracks per-turn + total AI cost.
//
// The state-machine logic lives in channel/state.ts (pure, unit-tested); this
// hook is the React/async glue.

import { useCallback, useMemo, useState } from 'react';
import {
  ApiError,
  channelAbandon,
  channelGo,
  channelSend,
} from '../api/client';
import type { DirectiveResult } from '../api/types';
import {
  canGo,
  canReply,
  isTerminal,
  nextSessionId,
  phaseForResult,
  type ChannelPhase,
} from './state';

/** A rendered turn in the thread. Local optimistic founder lines + CEO replies. */
export interface ThreadTurn {
  id: number;
  role: 'founder' | 'ceo';
  agentId?: string;
  text: string;
  kind: string;
  cost: number;
}

interface ChannelState {
  turns: ThreadTurn[];
  sessionId: string | null;
  phase: ChannelPhase;
  /** Last result (carries status / error_code / total_ai_cost). */
  last: DirectiveResult | null;
  /** Total AI cost accumulated across this session's turns. */
  totalCost: number;
  busy: boolean;
  /** Inline error (network / unexpected); LLM failures arrive as `failed` results. */
  error: string | null;
}

const INITIAL: ChannelState = {
  turns: [],
  sessionId: null,
  phase: 'idle',
  last: null,
  totalCost: 0,
  busy: false,
  error: null,
};

let turnSeq = 0;
function nextId(): number {
  turnSeq += 1;
  return turnSeq;
}

export interface UseChannel {
  turns: ThreadTurn[];
  phase: ChannelPhase;
  last: DirectiveResult | null;
  totalCost: number;
  busy: boolean;
  error: string | null;
  canReply: boolean;
  canGo: boolean;
  /** Send founder text: opens a session or replies on the open one. */
  send: (text: string) => Promise<void>;
  /** Fire-and-forget simple directive (no multi-turn session thread). */
  quickSend: (text: string) => Promise<void>;
  /** GO on a gated/proposed session — execute the held directive. */
  go: () => Promise<void>;
  /** Abandon the current gated/proposed session. */
  abandon: () => Promise<void>;
  /** Clear the thread to start fresh. */
  reset: () => void;
}

export function useChannel(): UseChannel {
  const [s, setS] = useState<ChannelState>(INITIAL);

  const applyResult = useCallback(
    (result: DirectiveResult, founderText?: string) => {
      setS((prev) => {
        const turns = [...prev.turns];
        if (founderText) {
          turns.push({
            id: nextId(),
            role: 'founder',
            text: founderText,
            kind: 'message',
            cost: 0,
          });
        }
        if (
          result.previous_agent_id &&
          result.previous_agent_id !== result.active_agent_id
        ) {
          turns.push({
            id: nextId(),
            role: 'ceo',
            agentId: result.active_agent_id,
            text: `${result.previous_agent_id.toUpperCase()} → ${result.active_agent_id.toUpperCase()}`,
            kind: 'handoff',
            cost: 0,
          });
        }
        // The CEO reply line is the result message; kind reflects the phase.
        const phase = phaseForResult(result);
        const ceoKind =
          phase === 'clarify'
            ? 'clarify_question'
            : phase === 'awaiting-go'
              ? 'preview'
              : phase === 'terminal-failed'
                ? 'final'
                : 'final';
        if (result.message) {
          turns.push({
            id: nextId(),
            role: 'ceo',
            agentId: result.active_agent_id,
            text: result.message,
            kind: ceoKind,
            cost: result.total_ai_cost ?? 0,
          });
        }
        // total_ai_cost is cumulative per the flattened result; take the max
        // so a clarify round (cost 0) doesn't shrink the running total.
        const totalCost = Math.max(prev.totalCost, result.total_ai_cost ?? 0);
        return {
          ...prev,
          turns,
          last: result,
          phase,
          sessionId: nextSessionId(result),
          totalCost,
          busy: false,
          error: null,
        };
      });
    },
    [],
  );

  const fail = useCallback((err: unknown) => {
    const msg =
      err instanceof ApiError
        ? err.status === 0
          ? 'Engine unreachable'
          : `Request failed (${err.status})`
        : err instanceof Error
          ? err.message
          : 'Unknown error';
    setS((prev) => ({ ...prev, busy: false, error: msg }));
  }, []);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      setS((prev) => ({ ...prev, busy: true, error: null }));
      // Reply on the open session only while clarifying; otherwise open fresh.
      const sessionId =
        canReply(phaseForResult(s.last)) ? s.sessionId : null;
      try {
        const result = await channelSend(trimmed, sessionId);
        applyResult(result, trimmed);
      } catch (err) {
        fail(err);
      }
    },
    [applyResult, fail, s.last, s.sessionId],
  );

  const quickSend = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      setS((prev) => ({ ...prev, busy: true, error: null }));
      try {
        const result = await channelSend(trimmed, null);
        applyResult(result, trimmed);
      } catch (err) {
        fail(err);
      }
    },
    [applyResult, fail],
  );

  const go = useCallback(async () => {
    if (!s.sessionId) return;
    setS((prev) => ({ ...prev, busy: true, error: null }));
    try {
      const result = await channelGo(s.sessionId);
      applyResult(result);
    } catch (err) {
      fail(err);
    }
  }, [applyResult, fail, s.sessionId]);

  const abandon = useCallback(async () => {
    if (!s.sessionId) return;
    setS((prev) => ({ ...prev, busy: true, error: null }));
    try {
      const result = await channelAbandon(s.sessionId);
      applyResult(result);
    } catch (err) {
      fail(err);
    }
  }, [applyResult, fail, s.sessionId]);

  const reset = useCallback(() => setS(INITIAL), []);

  return useMemo<UseChannel>(
    () => ({
      turns: s.turns,
      phase: s.phase,
      last: s.last,
      totalCost: s.totalCost,
      busy: s.busy,
      error: s.error,
      canReply: canReply(s.phase) && !isTerminal(s.phase),
      canGo: canGo(s.phase),
      send,
      quickSend,
      go,
      abandon,
      reset,
    }),
    [s, send, quickSend, go, abandon, reset],
  );
}
