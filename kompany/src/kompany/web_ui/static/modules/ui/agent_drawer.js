// Live agent drawer (right rail): what an agent is doing RIGHT NOW.
// Non-modal; the dashboard keeps updating behind it. Sections: header
// (role + status + current task), LIVE stream (harness/chat/spend lines
// from the store's per-role ring buffer, backfilled from /audit/recent
// on open), and the agent's task history (reuses episodes.agentWorkHTML).

import { store } from "/ui/static/modules/store.js?v=2";
import { agentWorkHTML } from "/ui/static/modules/ui/episodes.js?v=8";

const ROLE_NAMES = {
  ceo: "CEO", cfo: "CFO", cto: "CTO", cpo: "CPO", cmo: "CMO",
  cro: "CRO", coo: "COO", csa: "CSA", ciso: "CISO", cos: "CoS", cv: "CV",
  analyst: "Analyst", builder: "Builder", procurement: "Procurement",
  researcher: "Researcher", writer: "Writer",
};

// Kind glyph per activity line — terminal aesthetic, no emoji.
const KIND_GLYPH = {
  text: "›",
  tool_use: "⚙",
  turn: "…",
  spend: "$",
  status: "◆",
  session: "·",
  event: "·",
};

let _currentRole = null;
let _backfilled = false;
let _backfillDone = false;

function escapeHTML(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function roleLabel(r) {
  const key = String(r || "").toLowerCase();
  return ROLE_NAMES[key] || (r ? String(r).toUpperCase() : "?");
}

function fmtTime(ts) {
  const d = new Date(ts);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function lineHTML(line) {
  const glyph = KIND_GLYPH[line.kind] || "·";
  const cls = line.kind === "spend" ? "spend" : (line.kind === "status" ? "status" : "ev");
  return `<div class="drawer-line ${cls}"><span class="t">${fmtTime(line.ts)}</span><span class="g">${glyph}</span><span class="x">${escapeHTML(line.text) || "—"}</span></div>`;
}

function ensureDOM() {
  let el = document.getElementById("agent-drawer");
  if (el) return el;
  el = document.createElement("aside");
  el.id = "agent-drawer";
  el.className = "agent-drawer";
  el.innerHTML = `
    <div class="drawer-head">
      <span class="drawer-role" id="drawer-role">--</span>
      <span class="drawer-status" id="drawer-status">idle</span>
      <button type="button" class="drawer-close" id="drawer-close" title="Close (Esc)">[ x ]</button>
    </div>
    <div class="drawer-task" id="drawer-task" hidden></div>
    <div class="drawer-sec-label">// LIVE</div>
    <div class="drawer-stream" id="drawer-stream"><div class="empty">no activity yet — waiting for events.</div></div>
    <div class="drawer-sec-label">// TASKS</div>
    <div class="drawer-tasks" id="drawer-tasks"><div class="empty">loading…</div></div>`;
  document.body.appendChild(el);
  el.querySelector("#drawer-close").addEventListener("click", closeAgentDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && el.classList.contains("open")) closeAgentDrawer();
  });
  return el;
}

function agentRow(role) {
  const rows = store.state.agents || [];
  return rows.find((r) => String(r.role || "").toLowerCase() === role) || null;
}

function renderHeader() {
  const el = document.getElementById("agent-drawer");
  if (!el || !_currentRole) return;
  const row = agentRow(_currentRole);
  el.querySelector("#drawer-role").textContent = roleLabel(_currentRole);
  const status = row ? String(row.status || "idle").toLowerCase() : "idle";
  const statusEl = el.querySelector("#drawer-status");
  statusEl.textContent = status === "busy" || status === "running"
    ? "working"
    : (status === "awaiting" || status === "awaiting_user" || status === "blocked" ? "AWAIT YOU" : status);
  statusEl.className = `drawer-status ${status}`;
  const taskEl = el.querySelector("#drawer-task");
  const task = row && (row.current_task || row.last_action);
  if (task) {
    taskEl.hidden = false;
    taskEl.textContent = task;
  } else {
    taskEl.hidden = true;
  }
}

function renderStream() {
  const el = document.getElementById("agent-drawer");
  if (!el || !_currentRole) return;
  const host = el.querySelector("#drawer-stream");
  const lines = store.state.activity[_currentRole] || [];
  if (!lines.length) {
    // Truthful empty state: after the audit backfill returned nothing,
    // say so instead of implying data is still on its way.
    host.innerHTML = _backfillDone
      ? `<div class="empty">no recent activity for ${escapeHTML(roleLabel(_currentRole))} — live lines appear here when it works.</div>`
      : `<div class="empty">loading recent activity…</div>`;
    return;
  }
  // Auto-scroll only when the founder is already at the bottom.
  const nearBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 40;
  host.innerHTML = lines.map(lineHTML).join("");
  if (nearBottom) host.scrollTop = host.scrollHeight;
}

async function renderTasks() {
  const el = document.getElementById("agent-drawer");
  if (!el || !_currentRole) return;
  const host = el.querySelector("#drawer-tasks");
  host.innerHTML = `<div class="empty">loading…</div>`;
  try {
    host.innerHTML = await agentWorkHTML(_currentRole);
  } catch (e) {
    host.innerHTML = `<div class="empty">load failed: ${escapeHTML(e.message)}</div>`;
  }
}

// Backfill from the audit log — /events replays nothing, so a drawer
// opened mid-run would otherwise start blank. Deduped against the ring
// buffer by ts+text.
async function backfill(role) {
  try {
    const res = await fetch(`/audit/recent?limit=80`, { headers: { Accept: "application/json" } });
    if (!res.ok) return;
    const events = await res.json();
    const existing = new Set(
      (store.state.activity[role] || []).map((l) => `${l.ts}|${l.text}`),
    );
    const rows = (events || [])
      .filter((ev) => String(ev.agent_role || "").toLowerCase() === role)
      .map((ev) => ({
        // audit_log rows carry ``timestamp`` (SQLite default); tolerate
        // created_at for any future shape.
        ts: Date.parse(ev.timestamp || ev.created_at || "") || Date.now(),
        kind: "event",
        text: `${ev.event_type || ""}${ev.action ? " " + ev.action : ""}`.trim(),
        source: "audit",
      }))
      .filter((l) => l.text && !existing.has(`${l.ts}|${l.text}`));
    rows.sort((a, b) => a.ts - b.ts);
    for (const line of rows) store.pushActivity(role, line);
  } catch (_) { /* non-fatal */ }
  _backfillDone = true;
  renderStream();
}

export function openAgentDrawer(role) {
  const key = String(role || "").toLowerCase();
  if (!key) return;
  const switching = _currentRole !== key;
  _currentRole = key;
  const el = ensureDOM();
  el.classList.add("open");
  document.body.classList.add("drawer-open");
  if (switching || !_backfilled) {
    _backfilled = true;
    _backfillDone = false;
    backfill(key);
    renderTasks();
  }
  renderHeader();
  renderStream();
}

export function closeAgentDrawer() {
  _currentRole = null;
  _backfilled = false;
  _backfillDone = false;
  const el = document.getElementById("agent-drawer");
  if (el) el.classList.remove("open");
  document.body.classList.remove("drawer-open");
}

export function initAgentDrawer() {
  ensureDOM();
  store.subscribe("agents", renderHeader);
  store.subscribe("activity", ({ role }) => {
    if (role === _currentRole) {
      renderHeader();
      renderStream();
    }
  });
}
