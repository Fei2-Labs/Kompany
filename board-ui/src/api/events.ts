// Single shared SSE dispatcher. ONE EventSource('/events') for the whole
// app. The engine fans every event out on the default `message` channel
// as `{ type, data }` (core/event_hub.py). No history replay — only
// post-connect events — so the dispatcher is reconnect-safe: subscribers
// react to a trigger by refetching the authoritative REST source.

/** Refetch triggers the board reacts to. A single SSE event maps to one
 *  of these (or `null` for events the board ignores). */
export type RefetchTrigger =
  | 'inbox'
  | 'projects'
  | 'episodes'
  | 'spend'
  | 'runtime';

/** The `data` payload of an `llm.spend` SSE event (llm/cost_ledger.py). */
export interface LlmSpendData {
  action_type?: string;
  model?: string;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  run_id?: string | null;
  ledger_balance_after?: number;
}

/** SSE envelope as parsed off the wire. */
export interface SseEvent {
  type: string;
  data: Record<string, unknown>;
}

/**
 * Map an SSE `type` to the board source that needs refetching.
 *
 *   audit.approval.*            → inbox
 *   audit.project.* | harness.event → projects
 *   episode.recorded | audit.learning.episode_recorded → episodes
 *   llm.spend                   → spend (bump the metric, no refetch)
 *   audit.runtime.*             → runtime (engine suspended/resumed)
 *
 * Returns `null` for events the board does not react to (daemon.tick,
 * self_update.event, unrelated audit.* subtypes, …).
 *
 * Pure function — unit-tested in tests/events.test.ts.
 */
export function triggerForEvent(type: string): RefetchTrigger | null {
  if (type === 'llm.spend') return 'spend';
  if (type === 'episode.recorded') return 'episodes';
  if (type === 'harness.event') return 'projects';
  if (type.startsWith('audit.approval.')) return 'inbox';
  if (type.startsWith('audit.project.')) return 'projects';
  if (type === 'audit.learning.episode_recorded') return 'episodes';
  if (type.startsWith('audit.runtime.')) return 'runtime';
  return null;
}

type TriggerListener = (event: SseEvent) => void;

/**
 * Lazily-opened singleton EventSource with a subscribe API. Subscribers
 * register a callback per trigger; the dispatcher computes the trigger for
 * each incoming event and fans it out. The connection opens on the first
 * subscription and closes when the last subscriber leaves.
 */
class EventDispatcher {
  private source: EventSource | null = null;
  private readonly listeners = new Map<RefetchTrigger, Set<TriggerListener>>();
  // Raw "every event" subscribers (the activity timeline). Tracked separately
  // from the trigger map so the timeline sees events that map to no refetch
  // (daemon.tick, self_update.event, llm.spend, …).
  private readonly rawListeners = new Set<TriggerListener>();

  /** Subscribe to a refetch trigger. Returns an unsubscribe function. */
  subscribe(trigger: RefetchTrigger, fn: TriggerListener): () => void {
    let set = this.listeners.get(trigger);
    if (!set) {
      set = new Set();
      this.listeners.set(trigger, set);
    }
    set.add(fn);
    this.ensureOpen();
    return () => {
      const current = this.listeners.get(trigger);
      current?.delete(fn);
      if (current && current.size === 0) this.listeners.delete(trigger);
      this.maybeClose();
    };
  }

  /**
   * Subscribe to EVERY parsed SSE event (the activity timeline). Returns an
   * unsubscribe function. Keeps the connection open like a trigger subscriber.
   */
  subscribeAll(fn: TriggerListener): () => void {
    this.rawListeners.add(fn);
    this.ensureOpen();
    return () => {
      this.rawListeners.delete(fn);
      this.maybeClose();
    };
  }

  private maybeClose(): void {
    if (this.listeners.size === 0 && this.rawListeners.size === 0) this.close();
  }

  private ensureOpen(): void {
    if (this.source || typeof EventSource === 'undefined') return;
    const src = new EventSource('/events');
    src.onmessage = (ev: MessageEvent<string>) => this.handle(ev.data);
    // The browser auto-reconnects on error; nothing to replay, so we just
    // let subscribers keep their last-fetched state until the next event.
    this.source = src;
  }

  private handle(raw: string): void {
    let parsed: SseEvent;
    try {
      const obj = JSON.parse(raw) as unknown;
      if (
        !obj ||
        typeof obj !== 'object' ||
        typeof (obj as { type?: unknown }).type !== 'string'
      ) {
        return;
      }
      const { type, data } = obj as { type: string; data?: unknown };
      parsed = {
        type,
        data:
          data && typeof data === 'object'
            ? (data as Record<string, unknown>)
            : {},
      };
    } catch {
      return; // keepalive comments / malformed frames are dropped
    }
    // Raw subscribers (timeline) see every event, regardless of trigger.
    for (const fn of this.rawListeners) fn(parsed);
    const trigger = triggerForEvent(parsed.type);
    if (!trigger) return;
    const set = this.listeners.get(trigger);
    if (!set) return;
    for (const fn of set) fn(parsed);
  }

  private close(): void {
    this.source?.close();
    this.source = null;
  }
}

/** App-wide singleton. */
export const events = new EventDispatcher();
