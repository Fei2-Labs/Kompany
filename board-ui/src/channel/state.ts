// Pure CEO-channel state-machine helpers, derived from a flattened
// DirectiveResult. No fetch / React here so the transitions are unit-testable
// in isolation (tests/channelState.test.ts).
//
// Session lifecycle (state/models.py SessionStatus + DirectiveResult.status):
//   clarify              → CEO asks back; founder replies on the SAME session_id.
//   gated | proposed     → CEO held the action; founder hits GO or Abandon.
//   dispatched | answered → terminal success.
//   failed               → terminal failure (carries a classified error_code).
//
// `clarify`, `gated`, `proposed` are NON-terminal (the thread stays open);
// everything else is terminal.

import type { DirectiveResult } from '../api/types';

/** Coarse phase the UI renders the thread in. */
export type ChannelPhase =
  | 'idle' // no result yet
  | 'active' // a specialist owns the open conversation
  | 'clarify' // awaiting a founder text reply on the same session
  | 'awaiting-go' // gated/proposed — awaiting GO / Abandon
  | 'terminal-ok' // dispatched / answered
  | 'terminal-failed'; // failed

const CLARIFY = new Set(['clarify', 'clarifying']);
const AWAITING_GO = new Set(['gated', 'proposed']);
const TERMINAL_OK = new Set(['dispatched', 'answered']);

/** Map a DirectiveResult.status string to a coarse UI phase. */
export function phaseForStatus(status: string | null | undefined): ChannelPhase {
  if (!status) return 'idle';
  if (status === 'failed') return 'terminal-failed';
  if (CLARIFY.has(status)) return 'clarify';
  if (AWAITING_GO.has(status)) return 'awaiting-go';
  if (TERMINAL_OK.has(status)) return 'terminal-ok';
  // Unknown / future statuses: treat as terminal-ok so the thread closes
  // gracefully rather than hanging in a non-existent input state.
  return 'terminal-ok';
}

export function phaseForResult(result: DirectiveResult | null): ChannelPhase {
  if (result?.conversation_continues) return 'active';
  return phaseForStatus(result?.status);
}

/** The session can accept a free-text reply only while clarifying. */
export function canReply(phase: ChannelPhase): boolean {
  return phase === 'clarify' || phase === 'active';
}

/** GO / Abandon controls show only while a gated/proposed session waits. */
export function canGo(phase: ChannelPhase): boolean {
  return phase === 'awaiting-go';
}

/** A terminal phase means the thread is closed — start a fresh session next. */
export function isTerminal(phase: ChannelPhase): boolean {
  return phase === 'terminal-ok' || phase === 'terminal-failed';
}

/** Founder-safe label for a classified `error_code` (channel.py contract). */
export function errorLabel(code: string | undefined): string {
  switch (code) {
    case 'rate_limited':
      return 'Rate / quota limit hit — wait a moment and retry.';
    case 'unauthorized':
      return 'LLM provider rejected the API key — check it in Settings.';
    case 'network':
      return "Couldn't reach the LLM provider — check your connection.";
    case 'provider_error':
      return 'Provider rejected the request — try a different model in Settings.';
    default:
      return 'The CEO run failed — retry, or switch model/provider in Settings.';
  }
}

/** The id to keep replying on. Null once terminal (no session to continue). */
export function nextSessionId(result: DirectiveResult): string | null {
  if (result.conversation_continues) return result.session_id ?? null;
  const phase = phaseForStatus(result.status);
  if (isTerminal(phase)) return null;
  return result.session_id ?? null;
}
