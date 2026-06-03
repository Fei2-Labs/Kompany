// CEO channel — the founder's conversation surface with the team (via the
// CEO conductor). The directive bar becomes a collapsible thread anchored
// ABOVE the input; it expands upward as an overlay over the panels so it
// never steals panel space (PRD Decision 3, no-scroll invariant holds).
//
// Turn kinds rendered (server-defined): message (founder), clarify_question
// (CEO asks back, highlighted), preview (gated — GO/abandon buttons), final
// (CEO reply + cost chip), plus a client-only "progress" placeholder bubble
// streamed while a send is in flight (Decision 4).
//
// Design choices (documented per task):
//   * Input ownership: this module OWNS the #directive-input. directive.js is
//     left untouched for other pages but is NO LONGER wired on the dashboard
//     (app.js drops its initDirective call) — no dead dual handlers.
//   * Presentation: turns are flattened chronologically across sessions into a
//     single scrolling thread (cleanest for a terminal-style log). Each turn
//     carries its session_id so concurrent sessions interleave correctly and
//     update independently (Decision 6 — input is never disabled).
//   * Progress scoping: run_id is only known AFTER POST returns. While the POST
//     is in flight we show an UNSCOPED progress bubble (live activity lines +
//     a global-since-send cost delta). On response we attach the run_id and
//     from then on filter llm.spend / audit.* by run_id, then reconcile the
//     authoritative per-run cost via /channel/runs/{run_id}/cost.
//   * Active-session routing: a clarify (state=clarifying) or gated session is
//     the "active reply target". A founder message routes to it by default; a
//     new message after that session closes opens a fresh session (Decision 2).

import { api } from "/ui/static/modules/api.js";

const EXPAND_KEY = "kompany.channel.expanded";

let _root = null;       // .channel-panel overlay root
let _threadEl = null;   // scrolling thread container
let _summaryEl = null;  // collapsed one-line summary
let _input = null;      // #directive-input (shared with the bar)
let _statusEl = null;   // #directive-status
let _expanded = false;

// session_id -> { state, lastReply, runId, directiveId, projectId, approvalId }
const _sessions = new Map();
// session_id of the session a reply continues by default (clarify/gated).
let _activeReplyTarget = null;
// session_id -> timestamp; while the optimistic path is mid-flight (or just
// finished) for a session we suppress channel.updated reconcile to avoid a
// clobber/duplicate race. SSE channel.updated for clarify/gated can fire
// server-side BEFORE the POST response lands on the client.
const _optimisticActive = new Map();
function markOptimistic(sid) { if (sid) _optimisticActive.set(sid, Date.now()); }
function isOptimisticHot(sid) {
  const t = _optimisticActive.get(sid);
  return t != null && (Date.now() - t) < 2500;
}
// In-flight progress bubbles, keyed by a client turn id.
// { id, sessionId|null, runId|null, sinceCost, lines:[], el }
const _progress = new Map();
let _progressSeq = 0;

// Optimistic founder turns rendered before the server's session_id is known.
// A fresh question opens a NEW session, so onSubmit appends the founder bubble
// with sessionId=null (no data-session) and only back-fills data-session once
// the POST returns. A channel.updated SSE for that session can fire on the
// server BEFORE the POST response lands — at that moment the optimistic bubble
// has no data-session, so the old reconcile couldn't see it and re-appended the
// founder turn from the server snapshot, duplicating it (PR7 Part B).
//
// We track each optimistic founder element here so reconcile can find and drop
// it by session even when data-session hasn't been written yet. Keyed by a
// client turn id; ``sessionId`` is back-filled the moment we learn it.
// ``content`` lets us deterministically claim the right optimistic bubble when
// multiple brand-new sessions are posted before any POST returns.
const _pendingFounder = new Map(); // clientId -> { el, sessionId|null, content }
let _founderSeq = 0;

function escapeHTML(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtUsd(n) {
  const v = Number(n || 0);
  return "$" + v.toFixed(v >= 10 ? 2 : 4);
}

// Tiny markdown-ish: keep line breaks + bold, escape everything else.
function renderText(s) {
  let t = escapeHTML(s);
  t = t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  return t.replace(/\n/g, "<br>");
}

function isExpandedPref() {
  try { return localStorage.getItem(EXPAND_KEY) === "1"; } catch (_) { return false; }
}
function setExpandedPref(on) {
  try { localStorage.setItem(EXPAND_KEY, on ? "1" : "0"); } catch (_) {}
}

// --------------------------------------------------------------------------
// Expand / collapse
// --------------------------------------------------------------------------

function setExpanded(on) {
  _expanded = !!on;
  if (!_root) return;
  _root.classList.toggle("is-expanded", _expanded);
  _root.setAttribute("aria-expanded", _expanded ? "true" : "false");
  const toggle = _root.querySelector(".channel-toggle");
  if (toggle) toggle.textContent = _expanded ? "[ collapse ▾ ]" : "[ expand ▴ ]";
  setExpandedPref(_expanded);
  if (_expanded) scrollThreadToEnd();
}

function toggleExpanded() { setExpanded(!_expanded); }

function scrollThreadToEnd() {
  if (_threadEl) _threadEl.scrollTop = _threadEl.scrollHeight;
}

// --------------------------------------------------------------------------
// Thread rendering
// --------------------------------------------------------------------------

function clearThread() {
  if (_threadEl) _threadEl.innerHTML = "";
}

function emptyHintHTML() {
  return `<div class="channel-empty">no messages yet — type below to talk to your team.</div>`;
}

// Append a turn element. `turn`:
//   { role, kind, content, cost, sessionId, runId, directiveId,
//     projectId, approvalId, agents }
function turnHTML(turn) {
  const role = turn.role === "founder" ? "founder" : "ceo";
  const kind = turn.kind || "message";
  const who = role === "founder" ? "YOU" : "CEO";
  let body = renderText(turn.content || "");

  let extra = "";
  if (kind === "final") {
    const parts = [];
    if (turn.cost != null) parts.push(`cost ${fmtUsd(turn.cost)}`);
    const agents = turn.agents && turn.agents.length
      ? turn.agents.join(", ") : "";
    if (agents) parts.push(`agents: ${agents}`);
    if (parts.length) {
      extra += `<div class="channel-cost-chip">${escapeHTML(parts.join(" · "))}</div>`;
    }
    const links = [];
    if (turn.approvalId) {
      links.push(`<button type="button" class="channel-link" data-link-approval="${escapeHTML(turn.approvalId)}">→ inbox</button>`);
    }
    if (turn.projectId) {
      links.push(`<button type="button" class="channel-link" data-link-project="${escapeHTML(turn.projectId)}">→ episodes</button>`);
    }
    if (links.length) extra += `<div class="channel-links">${links.join("")}</div>`;
  }

  if (kind === "preview") {
    // A gate is only live while its session is still GATED. When a preview
    // turn is re-rendered for an already-resolved session (reload restore or
    // a late channel.updated reconcile after GO/abandon), render the buttons
    // inert so a closed gate can never re-arm a second dispatch.
    const live = !turn.sessionState || turn.sessionState === "gated";
    if (live) {
      extra += `<div class="channel-gate-actions" data-gate-session="${escapeHTML(turn.sessionId || "")}">
        <button type="button" class="key channel-go">[ GO ]</button>
        <button type="button" class="key danger channel-abandon">[ abandon ]</button>
      </div>`;
    } else {
      extra += `<div class="channel-gate-actions is-resolved-gate">
        <button type="button" class="key" disabled>[ GO ]</button>
        <button type="button" class="key danger" disabled>[ abandon ]</button>
      </div>`;
    }
  }

  const kindCls = `kind-${kind}`;
  const sid = turn.sessionId ? ` data-session="${escapeHTML(turn.sessionId)}"` : "";
  return `<div class="channel-turn role-${role} ${kindCls}"${sid}>
    <div class="channel-turn-who">${escapeHTML(who)}</div>
    <div class="channel-turn-body">${body}${extra}</div>
  </div>`;
}

function appendTurn(turn) {
  if (!_threadEl) return null;
  const hint = _threadEl.querySelector(".channel-empty");
  if (hint) hint.remove();
  const wrap = document.createElement("div");
  wrap.innerHTML = turnHTML(turn);
  const el = wrap.firstElementChild;
  _threadEl.appendChild(el);
  wireTurnButtons(el, turn);
  scrollThreadToEnd();
  return el;
}

function wireTurnButtons(el, turn) {
  el.querySelector(".channel-go")?.addEventListener("click", () => onGo(turn.sessionId, el));
  el.querySelector(".channel-abandon")?.addEventListener("click", () => onAbandon(turn.sessionId, el));
  el.querySelector("[data-link-approval]")?.addEventListener("click", (e) => {
    flashInbox(e.currentTarget.getAttribute("data-link-approval"));
  });
  el.querySelector("[data-link-project]")?.addEventListener("click", (e) => {
    flashEpisode(e.currentTarget.getAttribute("data-link-project"));
  });
}

// --------------------------------------------------------------------------
// Collapsed summary line
// --------------------------------------------------------------------------

function setSummary(text, cls) {
  if (!_summaryEl) return;
  _summaryEl.textContent = text || "no recent activity — click to open the channel.";
  _summaryEl.classList.remove("is-clarify", "is-gated", "is-busy");
  if (cls) _summaryEl.classList.add(cls);
}

// --------------------------------------------------------------------------
// Progress bubble (Decision 4)
// --------------------------------------------------------------------------

function startProgress() {
  const id = `p${++_progressSeq}`;
  const el = appendTurn({
    role: "ceo", kind: "progress",
    content: "working…",
  });
  if (el) el.setAttribute("data-progress", id);
  const entry = { id, sessionId: null, runId: null, sinceCost: 0, lines: [], el };
  _progress.set(id, entry);
  renderProgress(entry);
  setSummary("CEO is working…", "is-busy");
  return id;
}

function renderProgress(entry) {
  if (!entry.el) return;
  const body = entry.el.querySelector(".channel-turn-body");
  if (!body) return;
  const lines = entry.lines.slice(-6)
    .map((l) => `<div class="channel-prog-line">› ${escapeHTML(l)}</div>`).join("");
  body.innerHTML = `<div class="channel-prog">
    <span class="channel-prog-spin">▸</span> working…
    <span class="channel-prog-cost">${fmtUsd(entry.sinceCost)}</span>
  </div>${lines}`;
  scrollThreadToEnd();
}

function attachRunToProgress(id, runId, sessionId) {
  const entry = _progress.get(id);
  if (!entry) return;
  entry.runId = runId || entry.runId;
  entry.sessionId = sessionId || entry.sessionId;
}

// Replace a progress bubble with the final turn (or remove it on clarify/gate).
function resolveProgress(id, finalTurn) {
  const entry = _progress.get(id);
  if (!entry) return;
  if (entry.el) entry.el.remove();
  _progress.delete(id);
  if (finalTurn) appendTurn(finalTurn);
}

// llm.spend (carries run_id). Before run attach: accumulate into EVERY
// unscoped in-flight progress bubble (best-effort). After attach: only the
// matching run.
function onSpend(data) {
  const rid = data && data.run_id;
  const cost = Number((data && data.cost_usd) || 0);
  for (const entry of _progress.values()) {
    if (entry.runId) {
      if (rid && entry.runId === rid) { entry.sinceCost += cost; renderProgress(entry); }
    } else {
      // Not yet scoped — show activity, will reconcile from REST on resolve.
      entry.sinceCost += cost;
      renderProgress(entry);
    }
  }
}

// audit.* (carries run_id + directive_id). Drive status lines.
function onAudit(data) {
  const rid = data && data.run_id;
  const evt = (data && (data.event_type || data.action)) || "";
  if (!evt) return;
  const line = `${evt}${data.agent_role ? " [" + data.agent_role + "]" : ""}`;
  for (const entry of _progress.values()) {
    if (entry.runId) {
      if (rid && entry.runId === rid) { entry.lines.push(line); renderProgress(entry); }
    } else {
      entry.lines.push(line);
      renderProgress(entry);
    }
  }
}

// --------------------------------------------------------------------------
// Send flow
// --------------------------------------------------------------------------

function setStatus(text, cls) {
  if (!_statusEl) return;
  _statusEl.textContent = text || "";
  _statusEl.classList.remove("busy", "err", "ok");
  if (cls) _statusEl.classList.add(cls);
}

function rememberSession(result) {
  const sid = result.session_id;
  if (!sid) return;
  const entry = _sessions.get(sid) || {};
  entry.runId = result.run_id || entry.runId;
  entry.projectId = result.project_id || entry.projectId;
  entry.approvalId = result.approval_id || entry.approvalId;
  _sessions.set(sid, entry);
}

async function onSubmit() {
  const text = (_input.value || "").trim();
  if (!text) return;
  if (!_expanded) setExpanded(true);

  // Optimistic founder turn. Route to the active reply target by default.
  const sessionId = _activeReplyTarget || null;
  // Arm the optimistic guard for an already-known session BEFORE the POST so
  // a channel.updated that fires server-side mid-request can't clobber the
  // turn we just rendered (a clarify reply carries data-session immediately).
  if (sessionId) markOptimistic(sessionId);
  const founderEl = appendTurn({ role: "founder", kind: "message", content: text, sessionId });
  // Register the optimistic founder turn so a channel.updated reconcile can
  // dedup it even before the server session_id is back-filled onto data-session.
  const founderClientId = `f${++_founderSeq}`;
  if (founderEl) {
    founderEl.setAttribute("data-client-turn", founderClientId);
    _pendingFounder.set(founderClientId, {
      el: founderEl,
      sessionId: sessionId || null,
      content: text,
    });
  }
  _input.value = "";
  setStatus("sending…", "busy");

  const progressId = startProgress();
  if (sessionId) attachRunToProgress(progressId, null, sessionId);

  try {
    const result = await api.channelSend(text, sessionId);
    rememberSession(result);
    markOptimistic(result.session_id);
    // Back-fill the session_id the server assigned onto the optimistic
    // founder turn so channel.updated can match (and not clobber) it.
    if (founderEl && result.session_id) {
      founderEl.setAttribute("data-session", result.session_id);
      const pending = _pendingFounder.get(founderClientId);
      if (pending) pending.sessionId = result.session_id;
    }
    attachRunToProgress(progressId, result.run_id, result.session_id);
    await applyResult(result, progressId);
    // The optimistic founder bubble is now the authoritative one (back-filled
    // with data-session); drop its pending record so a later reconcile matches
    // it via data-session, not the unclaimed-founder fallback.
    _pendingFounder.delete(founderClientId);
    setStatus("", "");
  } catch (err) {
    _pendingFounder.delete(founderClientId);
    if (founderEl) founderEl.remove();
    resolveProgress(progressId, {
      role: "ceo", kind: "final", content: `error: ${err.message}`,
    });
    setStatus(`error: ${err.message}`, "err");
  } finally {
    _input.focus();
  }
}

// Turn a /channel/send | /go | /abandon result into thread state.
async function applyResult(result, progressId) {
  const status = result.status;
  const sid = result.session_id;

  if (status === "clarify") {
    resolveProgress(progressId, null);
    appendTurn({
      role: "ceo", kind: "clarify_question", content: result.message, sessionId: sid,
    });
    _activeReplyTarget = sid;
    if (sid) _sessions.set(sid, { ...(_sessions.get(sid) || {}), state: "clarifying" });
    setExpanded(true);
    if (_input) _input.placeholder = "reply to continue this conversation…";
    setSummary(`CEO asks: ${result.message}`, "is-clarify");
    return;
  }

  if (status === "gated") {
    resolveProgress(progressId, null);
    appendTurn({
      role: "ceo", kind: "preview", content: result.message, sessionId: sid,
    });
    _activeReplyTarget = sid;
    if (sid) _sessions.set(sid, { ...(_sessions.get(sid) || {}), state: "gated" });
    setExpanded(true);
    setSummary(`CEO needs approval to spend — [ GO ] to run.`, "is-gated");
    return;
  }

  // Any execute/answer terminal status -> final turn. Reconcile cost from
  // the authoritative per-run endpoint when available (covers cost streamed
  // before run_id was known / events missed during the POST).
  let cost = result.total_ai_cost;
  if (result.run_id) {
    try {
      const c = await api.channelRunCost(result.run_id);
      if (c && typeof c.total_cost === "number") cost = c.total_cost;
    } catch (_) {}
  }
  resolveProgress(progressId, {
    role: "ceo", kind: "final", content: result.message, sessionId: sid,
    cost, agents: result.agents_used || [],
    projectId: result.project_id, approvalId: result.approval_id,
  });

  // Terminal -> this session is no longer a reply target; next message starts fresh.
  if (sid === _activeReplyTarget) _activeReplyTarget = null;
  if (_input) _input.placeholder = "type a directive and press enter…";
  setSummary(result.message, "");
  if (sid) _sessions.set(sid, { ...(_sessions.get(sid) || {}), state: "closed" });
}

// --------------------------------------------------------------------------
// GO / abandon on a gated preview
// --------------------------------------------------------------------------

async function onGo(sessionId, turnEl) {
  if (!sessionId) return;
  markOptimistic(sessionId);
  disableGate(turnEl, "running…");
  const progressId = startProgress();
  attachRunToProgress(progressId, (_sessions.get(sessionId) || {}).runId, sessionId);
  try {
    const result = await api.channelGo(sessionId);
    rememberSession(result);
    markOptimistic(result.session_id);
    attachRunToProgress(progressId, result.run_id, result.session_id);
    if (turnEl) turnEl.classList.add("is-resolved");
    await applyResult(result, progressId);
  } catch (err) {
    resolveProgress(progressId, {
      role: "ceo", kind: "final", content: `GO failed: ${err.message}`,
    });
    enableGate(turnEl);
  }
}

async function onAbandon(sessionId, turnEl) {
  if (!sessionId) return;
  disableGate(turnEl, "abandoning…");
  try {
    const result = await api.channelAbandon(sessionId);
    if (turnEl) turnEl.classList.add("is-resolved");
    appendTurn({ role: "ceo", kind: "final", content: result.message || "Abandoned.", sessionId });
    if (sessionId === _activeReplyTarget) _activeReplyTarget = null;
    _sessions.set(sessionId, { ...(_sessions.get(sessionId) || {}), state: "closed" });
    setSummary("Session abandoned.", "");
  } catch (err) {
    enableGate(turnEl);
    setStatus(`abandon failed: ${err.message}`, "err");
  }
}

function disableGate(turnEl, label) {
  if (!turnEl) return;
  for (const b of turnEl.querySelectorAll(".channel-gate-actions button")) {
    b.disabled = true;
  }
  const go = turnEl.querySelector(".channel-go");
  if (go) go.textContent = `[ ${label} ]`;
}
function enableGate(turnEl) {
  if (!turnEl) return;
  for (const b of turnEl.querySelectorAll(".channel-gate-actions button")) {
    b.disabled = false;
  }
  const go = turnEl.querySelector(".channel-go");
  if (go) go.textContent = "[ GO ]";
}

// --------------------------------------------------------------------------
// Cross-links: flash an INBOX / EPISODES target
// --------------------------------------------------------------------------

function flashInbox(approvalId) {
  if (!approvalId) return;
  const short = String(approvalId).slice(0, 4);
  let target = document.querySelector(`.inbox-entry[data-id="${CSS.escape(approvalId)}"]`);
  if (!target) {
    // inbox renders only the short id in the visible line; match by data-id prefix.
    for (const el of document.querySelectorAll(".inbox-entry[data-id]")) {
      if ((el.getAttribute("data-id") || "").startsWith(approvalId) ||
          (el.getAttribute("data-id") || "").startsWith(short)) { target = el; break; }
    }
  }
  flashEl(target);
}

function flashEpisode(projectId) {
  if (!projectId) return;
  const target = document.querySelector(`.episode-item[data-pid="${CSS.escape(projectId)}"]`);
  flashEl(target);
}

function flashEl(el) {
  if (!el) return;
  el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  el.classList.add("channel-flash");
  setTimeout(() => el.classList.remove("channel-flash"), 1600);
}

// --------------------------------------------------------------------------
// Restore on load + channel.updated reconcile
// --------------------------------------------------------------------------

// Build a flat, chronological thread from the most recent sessions.
async function restore() {
  let sessions;
  try {
    const data = await api.channelSessions(20);
    sessions = (data && data.sessions) || [];
  } catch (_) {
    if (_threadEl) _threadEl.innerHTML = emptyHintHTML();
    return;
  }
  if (!sessions.length) {
    if (_threadEl) _threadEl.innerHTML = emptyHintHTML();
    return;
  }

  // Fetch turns for each session, then interleave by created_at.
  const details = await Promise.allSettled(
    sessions.map((s) => api.channelSession(s.session_id)),
  );
  const turns = [];
  for (const d of details) {
    if (d.status !== "fulfilled" || !d.value) continue;
    const session = d.value.session || {};
    _sessions.set(session.session_id, {
      state: session.state, runId: session.run_id,
      projectId: session.project_id, approvalId: session.approval_id,
    });
    for (const t of d.value.turns || []) {
      turns.push({ ...t, _session: session });
    }
  }
  turns.sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")));

  clearThread();
  if (!turns.length) { _threadEl.innerHTML = emptyHintHTML(); }
  let lastReply = "";
  let activeClarify = null, activeGate = null;
  for (const t of turns) {
    const s = t._session || {};
    appendTurn({
      role: t.role, kind: t.kind, content: t.content, cost: t.cost,
      sessionId: s.session_id, runId: t.run_id, directiveId: t.directive_id,
      projectId: s.project_id, approvalId: s.approval_id, sessionState: s.state,
    });
    if (t.role === "ceo") lastReply = t.content || lastReply;
    if (t.kind === "clarify_question" && s.state === "clarifying") activeClarify = s.session_id;
    if (t.kind === "preview" && s.state === "gated") activeGate = s.session_id;
  }

  // Resume an in-flight session: re-arm reply routing + reconcile its cost.
  _activeReplyTarget = activeClarify || activeGate || null;
  if (activeClarify) {
    if (_input) _input.placeholder = "reply to continue this conversation…";
    setSummary(lastReply ? `CEO asks: ${lastReply}` : "CEO is waiting on your reply.", "is-clarify");
  } else if (activeGate) {
    setSummary("CEO needs approval to spend — [ GO ] to run.", "is-gated");
  } else {
    setSummary(lastReply || "no recent activity — click to open the channel.", "");
  }

  // Restored turns already carry their persisted ``cost`` (final turns) from
  // the session detail, and any still-streaming run reconciles live via the
  // channel.updated -> refetch path. No per-session cost prefetch is needed on
  // load — it was previously fetched and discarded (N wasted serial requests).
}

// channel.updated SSE -> refetch the affected session and patch the thread.
async function onChannelUpdated(data) {
  const sid = data && data.session_id;
  if (!sid) return;
  let detail;
  try {
    detail = await api.channelSession(sid);
  } catch (_) { return; }
  if (!detail) return;
  const session = detail.session || {};
  _sessions.set(sid, {
    state: session.state, runId: session.run_id,
    projectId: session.project_id, approvalId: session.approval_id,
  });

  // While a send/GO for this session is in flight (or just settled), the
  // optimistic path owns rendering (it appends the authoritative turn + the
  // reconciled cost when the POST resolves). Skip SSE reconcile to avoid a
  // clobber/duplicate race — channel.updated can fire server-side BEFORE the
  // POST response reaches the client.
  if (isOptimisticHot(sid)) return;
  for (const entry of _progress.values()) {
    if (entry.sessionId && entry.sessionId === sid) return;
  }

  // Replace any existing turns for this session in place: remove this
  // session's turns, re-append from the fresh detail. Turns we already
  // rendered optimistically for THIS session are removed so we don't
  // double-render.
  const existing = _threadEl
    ? Array.from(_threadEl.querySelectorAll(`.channel-turn[data-session="${CSS.escape(sid)}"]`))
    : [];
  // Fold in any optimistic founder bubble for this session that does NOT yet
  // carry data-session (the POST hasn't back-filled it). Without this the
  // founder turn would survive removal and the server's copy would be appended
  // alongside it — the duplicate "YOU" turn this fix targets.
  const existingSet = new Set(existing);
  // Unclaimed optimistic founder bubbles (sessionId still null because the POST
  // hasn't returned yet). Match them deterministically against the server's
  // founder turn content so overlapping new-session sends still dedup cleanly.
  const founderServerText = (detail.turns || []).find((t) => t.role === "founder")?.content || null;
  for (const [cid, pending] of _pendingFounder.entries()) {
    if (!pending.el || !pending.el.isConnected) {
      _pendingFounder.delete(cid);
      continue;
    }
    if (pending.sessionId === sid && !existingSet.has(pending.el)) {
      existing.push(pending.el);
      existingSet.add(pending.el);
      continue;
    }
    if (
      pending.sessionId == null &&
      founderServerText != null &&
      pending.content === founderServerText &&
      !existingSet.has(pending.el)
    ) {
      pending.sessionId = sid;
      existing.push(pending.el);
      existingSet.add(pending.el);
    }
  }
  // Only reconcile when the server has at least as many turns as we show
  // (avoids clobbering an in-flight render with a thinner intermediate
  // server snapshot).
  if ((detail.turns || []).length < existing.length) return;
  for (const el of existing) {
    const cid = el.getAttribute("data-client-turn");
    if (cid) _pendingFounder.delete(cid);
    el.remove();
  }
  for (const t of detail.turns || []) {
    appendTurn({
      role: t.role, kind: t.kind, content: t.content, cost: t.cost,
      sessionId: sid, runId: t.run_id, directiveId: t.directive_id,
      projectId: session.project_id, approvalId: session.approval_id,
      sessionState: session.state,
    });
  }
}

// --------------------------------------------------------------------------
// Public init + SSE bridge
// --------------------------------------------------------------------------

export function initChannel() {
  _input = document.getElementById("directive-input");
  _statusEl = document.getElementById("directive-status");
  if (!_input) return;

  // Build the overlay panel and insert it just above the directive bar.
  const bar = document.querySelector(".directive-bar");
  _root = document.createElement("div");
  _root.className = "channel-panel frame";
  _root.setAttribute("data-label", "CHANNEL // CEO");
  _root.innerHTML = `
    <div class="channel-head">
      <span class="channel-summary" id="channel-summary"></span>
      <button type="button" class="channel-toggle">[ expand ▴ ]</button>
    </div>
    <div class="channel-thread" id="channel-thread"></div>
  `;
  if (bar && bar.parentElement) {
    bar.parentElement.insertBefore(_root, bar);
  } else {
    document.body.appendChild(_root);
  }
  _threadEl = _root.querySelector("#channel-thread");
  _summaryEl = _root.querySelector("#channel-summary");

  // Pull the [ LOG ] timeline trigger into the channel head so every bottom
  // affordance lives in one thin strip — kills the floating-FAB overlap.
  // timeline_modal.js already bound its click handler (inits earlier); moving
  // the node keeps the listener.
  const fab = document.getElementById("timeline-fab");
  const head = _root.querySelector(".channel-head");
  if (fab && head) head.insertBefore(fab, head.querySelector(".channel-toggle"));

  _root.querySelector(".channel-toggle").addEventListener("click", toggleExpanded);
  _summaryEl.addEventListener("click", () => { if (!_expanded) setExpanded(true); });

  // Input owns submission (replaces directive.js on the dashboard).
  _input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); onSubmit(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && _expanded) setExpanded(false);
  });

  setSummary("", "");
  setExpanded(isExpandedPref());

  restore();
}

// Called from app.js handleEvent so the channel reuses the single SSE feed.
export function channelHandleEvent(type, data) {
  if (type === "llm.spend") onSpend(data || {});
  else if (type === "channel.updated") onChannelUpdated(data || {});
  else if (type && type.startsWith("audit.")) onAudit(data || {});
}
