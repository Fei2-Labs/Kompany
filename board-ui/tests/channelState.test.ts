import { describe, expect, it } from 'vitest';
import {
  canGo,
  canReply,
  errorLabel,
  isTerminal,
  nextSessionId,
  phaseForStatus,
} from '../src/channel/state';
import type { DirectiveResult } from '../src/api/types';

function result(over: Partial<DirectiveResult>): DirectiveResult {
  return {
    status: 'answered',
    message: '',
    project_id: null,
    approval_id: null,
    total_ai_cost: 0,
    agents_used: [],
    run_id: null,
    session_id: null,
    ...over,
  };
}

describe('phaseForStatus (DirectiveResult → UI phase)', () => {
  it('maps idle when no status', () => {
    expect(phaseForStatus(undefined)).toBe('idle');
    expect(phaseForStatus(null)).toBe('idle');
    expect(phaseForStatus('')).toBe('idle');
  });

  it('maps clarify / clarifying to clarify', () => {
    expect(phaseForStatus('clarify')).toBe('clarify');
    expect(phaseForStatus('clarifying')).toBe('clarify');
  });

  it('maps gated and proposed to awaiting-go', () => {
    expect(phaseForStatus('gated')).toBe('awaiting-go');
    expect(phaseForStatus('proposed')).toBe('awaiting-go');
  });

  it('maps dispatched and answered to terminal-ok', () => {
    expect(phaseForStatus('dispatched')).toBe('terminal-ok');
    expect(phaseForStatus('answered')).toBe('terminal-ok');
  });

  it('maps failed to terminal-failed', () => {
    expect(phaseForStatus('failed')).toBe('terminal-failed');
  });

  it('treats unknown statuses as terminal-ok (closes gracefully)', () => {
    expect(phaseForStatus('something_new')).toBe('terminal-ok');
  });
});

describe('phase predicates', () => {
  it('allows a text reply only while clarifying', () => {
    expect(canReply('clarify')).toBe(true);
    expect(canReply('awaiting-go')).toBe(false);
    expect(canReply('terminal-ok')).toBe(false);
    expect(canReply('idle')).toBe(false);
  });

  it('shows GO/Abandon only while awaiting-go', () => {
    expect(canGo('awaiting-go')).toBe(true);
    expect(canGo('clarify')).toBe(false);
    expect(canGo('terminal-failed')).toBe(false);
  });

  it('marks terminal-ok and terminal-failed as terminal', () => {
    expect(isTerminal('terminal-ok')).toBe(true);
    expect(isTerminal('terminal-failed')).toBe(true);
    expect(isTerminal('clarify')).toBe(false);
    expect(isTerminal('awaiting-go')).toBe(false);
  });
});

describe('state-machine transitions', () => {
  it('clarify round keeps the session id alive to reply on', () => {
    const r = result({ status: 'clarify', session_id: 'sess-1' });
    expect(phaseForStatus(r.status)).toBe('clarify');
    expect(nextSessionId(r)).toBe('sess-1');
  });

  it('gated/proposed keeps the session id for GO', () => {
    expect(nextSessionId(result({ status: 'gated', session_id: 'sess-2' }))).toBe('sess-2');
    expect(nextSessionId(result({ status: 'proposed', session_id: 'sess-3' }))).toBe('sess-3');
  });

  it('terminal results drop the session id (no thread to continue)', () => {
    expect(nextSessionId(result({ status: 'dispatched', session_id: 'sess-4' }))).toBeNull();
    expect(nextSessionId(result({ status: 'answered', session_id: 'sess-5' }))).toBeNull();
    expect(nextSessionId(result({ status: 'failed', session_id: 'sess-6' }))).toBeNull();
  });

  it('full clarify → gated → dispatched walk', () => {
    const clarify = result({ status: 'clarify', session_id: 's' });
    expect(phaseForStatus(clarify.status)).toBe('clarify');
    expect(canReply(phaseForStatus(clarify.status))).toBe(true);

    const gated = result({ status: 'gated', session_id: 's' });
    expect(canGo(phaseForStatus(gated.status))).toBe(true);
    expect(nextSessionId(gated)).toBe('s');

    const done = result({ status: 'dispatched', session_id: 's' });
    expect(isTerminal(phaseForStatus(done.status))).toBe(true);
    expect(nextSessionId(done)).toBeNull();
  });
});

describe('errorLabel (classified failure codes)', () => {
  it('renders a founder-safe label per code', () => {
    expect(errorLabel('rate_limited')).toMatch(/rate|quota/i);
    expect(errorLabel('unauthorized')).toMatch(/key/i);
    expect(errorLabel('network')).toMatch(/reach|connection/i);
    expect(errorLabel('provider_error')).toMatch(/provider|model/i);
  });

  it('falls back to a generic message for an unknown code', () => {
    expect(errorLabel(undefined)).toMatch(/failed/i);
    expect(errorLabel('mystery')).toMatch(/failed/i);
  });
});
