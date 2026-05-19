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
    };
  }

  update(section, data) {
    this.state[section] = data;
    this.dispatchEvent(new CustomEvent(`update:${section}`, { detail: data }));
  }

  subscribe(section, handler) {
    this.addEventListener(`update:${section}`, (evt) => handler(evt.detail));
  }
}

export const store = new Store();
