import fs from "node:fs";
import vm from "node:vm";

class FakeClassList {
  constructor() {
    this._set = new Set();
  }
  add(...names) {
    for (const name of names) this._set.add(name);
  }
  remove(...names) {
    for (const name of names) this._set.delete(name);
  }
  toggle(name, force) {
    if (force === undefined) {
      if (this._set.has(name)) {
        this._set.delete(name);
        return false;
      }
      this._set.add(name);
      return true;
    }
    if (force) this._set.add(name);
    else this._set.delete(name);
    return !!force;
  }
  contains(name) {
    return this._set.has(name);
  }
}

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = String(tagName).toUpperCase();
    this.dataset = {};
    this.attributes = new Map();
    this.children = [];
    this.parentElement = null;
    this.ownerDocument = null;
    this.classList = new FakeClassList();
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.textContent = "";
    this.value = "";
    this.placeholder = "";
  }

  setAttribute(name, value) {
    const val = String(value);
    this.attributes.set(name, val);
    if (name === "class") {
      this.classList = new FakeClassList();
      for (const cls of val.split(/\s+/).filter(Boolean)) this.classList.add(cls);
    } else if (name.startsWith("data-")) {
      const key = name
        .slice(5)
        .replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
      this.dataset[key] = val;
    }
  }

  getAttribute(name) {
    if (name.startsWith("data-")) {
      const key = name
        .slice(5)
        .replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
      return this.dataset[key] ?? null;
    }
    return this.attributes.get(name) ?? null;
  }

  appendChild(child) {
    child.parentElement = this;
    child.ownerDocument = this.ownerDocument;
    this.children.push(child);
    return child;
  }

  remove() {
    if (!this.parentElement) return;
    const siblings = this.parentElement.children;
    const idx = siblings.indexOf(this);
    if (idx >= 0) siblings.splice(idx, 1);
    this.parentElement = null;
  }

  addEventListener() {}
  focus() {}
  scrollIntoView() {}

  get isConnected() {
    let node = this;
    while (node) {
      if (node === this.ownerDocument?.body) return true;
      node = node.parentElement;
    }
    return false;
  }

  querySelectorAll(selector) {
    const parts = selector.trim().split(/\s+/);
    let nodes = [this];
    for (const part of parts) {
      const next = [];
      for (const node of nodes) next.push(...node._collect(part));
      nodes = next;
    }
    return nodes;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  _collect(part) {
    const found = [];
    for (const child of this.children) {
      if (matchesPart(child, part)) found.push(child);
      found.push(...child._collect(part));
    }
    return found;
  }
}

class FakeDocument {
  constructor() {
    this.body = new FakeElement("body");
    this.body.ownerDocument = this;
  }

  createElement(tag) {
    const el = new FakeElement(tag);
    el.ownerDocument = this;
    return el;
  }

  querySelectorAll(selector) {
    return this.body.querySelectorAll(selector);
  }

  querySelector(selector) {
    return this.body.querySelector(selector);
  }

  addEventListener() {}
}

function matchesPart(el, part) {
  const classes = [...part.matchAll(/\.([A-Za-z0-9_-]+)/g)].map((m) => m[1]);
  for (const cls of classes) {
    if (!el.classList.contains(cls)) return false;
  }
  const attrs = [...part.matchAll(/\[([^=\]]+)="([^"]*)"\]/g)];
  for (const [, name, value] of attrs) {
    if (el.getAttribute(name) !== value) return false;
  }
  return true;
}

function founderCount(root) {
  return root.querySelectorAll(".channel-turn.role-founder").length;
}

function makeTurn({ role, sessionId, content, clientTurn }) {
  const el = new FakeElement("div");
  el.ownerDocument = document;
  el.classList.add("channel-turn", role === "founder" ? "role-founder" : "role-ceo");
  if (sessionId) el.setAttribute("data-session", sessionId);
  if (clientTurn) el.setAttribute("data-client-turn", clientTurn);
  el.textContent = content;
  return el;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const document = new FakeDocument();
const thread = document.createElement("div");
thread.classList.add("channel-thread");
document.body.appendChild(thread);

const apiCalls = [];
const api = {
  async channelSession(id) {
    apiCalls.push(id);
    return {
      session: {
        session_id: id,
        state: "answered",
        run_id: null,
        project_id: null,
        approval_id: null,
      },
      turns: [
        { role: "founder", kind: "message", content: "现在团队正在进行的任务有哪些", cost: 0, run_id: null, directive_id: "d1" },
        { role: "ceo", kind: "final", content: "团队正在推进 1 个项目。", cost: 0.12, run_id: null, directive_id: "d1" },
      ],
    };
  },
};

const source = fs.readFileSync(
  new URL("../src/kompany/web_ui/static/modules/ui/channel.js", import.meta.url),
  "utf8",
);
const sanitized = source
  .replace(/^import .*?;\n/m, "")
  .replace(/export function initChannel/g, "function initChannel")
  .replace(/export function channelHandleEvent/g, "function channelHandleEvent");

const context = vm.createContext({
  console,
  api,
  document,
  localStorage: { getItem: () => null, setItem: () => {} },
  CSS: { escape: (s) => String(s) },
  setTimeout: () => 0,
  clearTimeout: () => {},
  Date,
});
vm.runInContext(sanitized, context, { filename: "channel.js" });

vm.runInContext(`
  _threadEl = document.querySelector('.channel-thread');
  _summaryEl = document.createElement('div');
  _statusEl = document.createElement('div');
  _input = document.createElement('input');
  appendTurn = (turn) => {
    const el = document.createElement('div');
    el.classList.add('channel-turn', turn.role === 'founder' ? 'role-founder' : 'role-ceo');
    if (turn.sessionId) el.setAttribute('data-session', turn.sessionId);
    if (turn.kind) el.classList.add('kind-' + turn.kind);
    el.textContent = turn.content || '';
    _threadEl.appendChild(el);
    return el;
  };
  wireTurnButtons = () => {};
  scrollThreadToEnd = () => {};
`, context);

const optimistic = makeTurn({
  role: "founder",
  sessionId: null,
  content: "现在团队正在进行的任务有哪些",
  clientTurn: "f1",
});
thread.appendChild(optimistic);
vm.runInContext(
  `_pendingFounder.set('f1', { el: document.querySelector('.role-founder'), sessionId: null, content: '现在团队正在进行的任务有哪些' });`,
  context,
);

await context.onChannelUpdated({ session_id: "sess-1" });
assert(founderCount(thread) === 1, "new-session reconcile should leave exactly one founder turn");
assert(apiCalls.length === 1 && apiCalls[0] === "sess-1", "channelSession should be fetched for the SSE session");

thread.children.length = 0;
vm.runInContext("_pendingFounder.clear(); _sessions.clear();", context);
const existing = makeTurn({
  role: "founder",
  sessionId: "sess-2",
  content: "补充一下，邮箱渠道",
  clientTurn: "f2",
});
thread.appendChild(existing);
vm.runInContext(
  `_pendingFounder.set('f2', { el: document.querySelector('.role-founder'), sessionId: 'sess-2', content: '补充一下，邮箱渠道' });`,
  context,
);
api.channelSession = async (id) => ({
  session: {
    session_id: id,
    state: "clarifying",
    run_id: null,
    project_id: null,
    approval_id: null,
  },
  turns: [
    { role: "founder", kind: "message", content: "补充一下，邮箱渠道", cost: 0, run_id: null, directive_id: "d2" },
    { role: "ceo", kind: "clarify_question", content: "目标受众是谁？", cost: 0.05, run_id: null, directive_id: "d2" },
  ],
});

await context.onChannelUpdated({ session_id: "sess-2" });
assert(founderCount(thread) === 1, "known-session clarify reconcile should leave exactly one founder turn");

thread.children.length = 0;
vm.runInContext("_pendingFounder.clear(); _sessions.clear(); _activeReplyTarget = null;", context);
api.channelSend = async () => {
  throw new Error("send failed");
};
vm.runInContext("_input.value = '失败消息';", context);
await context.onSubmit();
assert(founderCount(thread) === 0, "failed send should remove the optimistic founder turn");
assert(
  vm.runInContext("_pendingFounder.size", context) === 0,
  "failed send should clear pending founder entries"
);
assert(
  thread.querySelectorAll(".channel-turn.role-ceo").length === 1,
  "failed send should leave only the CEO error turn"
);

console.log("channel UI dedup smoke: ok");
