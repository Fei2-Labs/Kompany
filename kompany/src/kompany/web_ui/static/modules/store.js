// Simple event-driven store. Each section (agents, inbox, episodes,
// status, sse) is keyed independently so UI modules only re-render when
// their slice changes.

class Store extends EventTarget {
  constructor() {
    super();
    this.state = {
      agents: [],
      inbox: [],
      episodes: [],
      status: {},
      sse: { online: false },
      // Open watchdog health events (NEEDS YOU feed).
      health: [],
      // BLOCKED tasks (connect/approve asks) from active projects (NEEDS YOU).
      blocked: [],
      // Per-agent live activity ring buffers (agent drawer). Keyed by
      // lowercase role -> array of { ts, kind, text, source }, newest
      // last, capped at ACTIVITY_CAP. Updated via pushActivity, which
      // notifies subscribers with { role, lines } so only the open
      // drawer re-renders.
      activity: {},
    };
  }

  update(section, data) {
    this.state[section] = data;
    this.dispatchEvent(new CustomEvent(`update:${section}`, { detail: data }));
  }

  subscribe(section, handler) {
    this.addEventListener(`update:${section}`, (evt) => handler(evt.detail));
  }

  pushActivity(role, line) {
    const key = String(role || "").toLowerCase();
    if (!key || !line) return;
    const buf = this.state.activity[key] || (this.state.activity[key] = []);
    buf.push(line);
    if (buf.length > ACTIVITY_CAP) buf.splice(0, buf.length - ACTIVITY_CAP);
    this.dispatchEvent(
      new CustomEvent("update:activity", { detail: { role: key, lines: buf } }),
    );
  }
}

const ACTIVITY_CAP = 200;

export const store = new Store();
