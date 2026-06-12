// Right drawer-ish episode list + payload viewer.

import { api } from "/ui/static/modules/api.js";

function escapeHTML(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

let _activeId = null;

const ROLE_NAMES = {
  ceo: "CEO", cfo: "CFO", cto: "CTO", cpo: "CPO", cmo: "CMO",
  cro: "CRO", coo: "COO", csa: "CSA", ciso: "CISO", cos: "CoS", cv: "CV",
  analyst: "Analyst", builder: "Builder", procurement: "Procurement",
  researcher: "Researcher", writer: "Writer",
};
const SUBAGENTS = new Set(["analyst", "builder", "procurement", "researcher", "writer"]);

function roleLabel(r) {
  const key = String(r || "").toLowerCase();
  return ROLE_NAMES[key] || (r ? String(r).toUpperCase() : "?");
}

// Parse a task ``result`` (JSON string or object) into a plain object
// so we can read output + outcome + founder_action.
function parseResult(result) {
  let r = result;
  if (typeof r === "string") {
    try { r = JSON.parse(r); } catch (_) { return { output: r }; }
  }
  return r && typeof r === "object" ? r : { output: String(r || "") };
}

// The task ``result`` is a JSON string like {"output": "...markdown..."}.
// Pull out the human text; fall back to the raw string.
function taskOutput(result) {
  if (!result) return "";
  let r = result;
  if (typeof r === "string") {
    try { r = JSON.parse(r); } catch (_) { return r; }
  }
  if (r && typeof r === "object") return r.output || r.text || JSON.stringify(r);
  return String(r);
}

function fmtUsd(n) {
  const v = Number(n || 0);
  return "$" + v.toFixed(v >= 10 ? 2 : 4);
}

// Render a markdown-ish output block as readable HTML: keep line breaks,
// bold **x**, and headings. Deliberately tiny — not a full md parser.
function renderText(s) {
  let t = escapeHTML(String(s || ""));
  t = t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  t = t.replace(/^### (.+)$/gm, '<span class="ep-h">$1</span>');
  t = t.replace(/^## (.+)$/gm, '<span class="ep-h">$1</span>');
  t = t.replace(/^# (.+)$/gm, '<span class="ep-h">$1</span>');
  return t.replace(/\n/g, "<br>");
}

async function loadPayload(projectId) {
  const host = document.getElementById("episodes-payload");
  if (!host) return;
  host.innerHTML = `<div class="empty">loading ${escapeHTML(projectId)}…</div>`;
  try {
    const row = await api.episode(projectId);
    let payload = row.payload_json || row.payload || row;
    if (typeof payload === "string") {
      try { payload = JSON.parse(payload); } catch (_) {}
    }
    host.innerHTML = renderEpisodeDetail(payload);
    // Wire per-task expand toggles.
    for (const head of host.querySelectorAll(".ep-task-head")) {
      head.addEventListener("click", () => {
        const card = head.closest(".ep-task");
        if (card) card.classList.toggle("open");
      });
    }
    // Raw JSON escape hatch for power users.
    const rawToggle = host.querySelector(".ep-raw-toggle");
    if (rawToggle) {
      rawToggle.addEventListener("click", () => {
        const pre = host.querySelector(".ep-raw");
        if (pre) {
          pre.hidden = !pre.hidden;
          if (!pre.textContent) pre.textContent = JSON.stringify(payload, null, 2);
        }
      });
    }
  } catch (e) {
    host.innerHTML = `<div class="empty">load failed: ${escapeHTML(e.message)}</div>`;
  }
}

function renderEpisodeDetail(p) {
  if (!p || typeof p !== "object") {
    return `<div class="empty">no payload.</div>`;
  }
  const meta = p.project_meta || {};
  const tasks = Array.isArray(p.tasks) ? p.tasks : [];
  const done = tasks.filter((t) => t.status === "completed").length;
  const ledger = p.ledger_summary || {};
  const aiCost = Number(ledger.ai_cost || 0);
  const decisions = (p.decisions || []).length;
  const debates = (p.debate_ids || []).length;

  // Which agents took part + how many tasks each — so the founder can
  // see who did the work (and whether any subagents were used).
  const byAgent = {};
  for (const t of tasks) {
    const a = String(t.assigned_agent || "?").toLowerCase();
    byAgent[a] = (byAgent[a] || 0) + 1;
  }
  const agentChips = Object.entries(byAgent)
    .map(([a, n]) => {
      const sub = SUBAGENTS.has(a) ? " ep-chip-sub" : "";
      return `<span class="ep-chip${sub}">${escapeHTML(roleLabel(a))} ×${n}${SUBAGENTS.has(a) ? " ⚙" : ""}</span>`;
    })
    .join("");

  const STATUS_CLS = { completed: "ok", delivered: "deliv", blocked: "err", failed: "err" };
  const STATUS_LABEL = {
    completed: "DONE (executed)",
    delivered: "DELIVERED — your move",
    blocked: "BLOCKED — needs integration",
    failed: "failed",
  };
  let actionCount = 0;

  const taskCards = tasks.map((t, i) => {
    const agent = roleLabel(t.assigned_agent);
    const sub = SUBAGENTS.has(String(t.assigned_agent || "").toLowerCase());
    const result = parseResult(t.result);
    const out = result.output || taskOutput(t.result);
    const st = String(t.status || "").toLowerCase();
    const statusCls = STATUS_CLS[st] || "warn";
    const statusLabel = STATUS_LABEL[st] || (t.status || "?");
    const founderAction = result.founder_action || "";
    if (st === "delivered" || st === "blocked") actionCount += 1;
    return `
      <div class="ep-task">
        <div class="ep-task-head">
          <span class="ep-task-ix">${i + 1}</span>
          <span class="ep-task-agent${sub ? " ep-chip-sub" : ""}">${escapeHTML(agent)}${sub ? " ⚙" : ""}</span>
          <span class="ep-task-title">${escapeHTML(t.title || "(untitled)")}</span>
          <span class="ep-task-status ${statusCls}">${escapeHTML(statusLabel)}</span>
          <span class="ep-task-caret">▸</span>
        </div>
        <div class="ep-task-body">
          ${founderAction ? `<div class="ep-task-action">▸ YOUR MOVE: ${escapeHTML(founderAction)}</div>` : ""}
          ${out ? `<div class="ep-task-output">${renderText(out)}</div>` : `<div class="empty">no output recorded.</div>`}
        </div>
      </div>`;
  }).join("");

  const actionBanner = actionCount > 0
    ? `<div class="ep-action-banner">⚠ ${actionCount} task${actionCount === 1 ? "" : "s"} need YOU to act — the team produced the assets but can't send/publish without integrations. Open each task for the handoff.</div>`
    : "";

  return `
    <div class="ep-detail">
      <div class="ep-detail-head">
        <div class="ep-detail-name">${escapeHTML(meta.name || p.project_id || "(unnamed)")}</div>
        <div class="ep-detail-stats">
          <span class="ep-stat ${meta.status === "completed" ? "ok" : "warn"}">${escapeHTML(meta.status || "?")}</span>
          <span class="ep-stat">${done}/${tasks.length} tasks done</span>
          <span class="ep-stat">AI cost ${fmtUsd(aiCost)}</span>
          ${decisions ? `<span class="ep-stat">${decisions} decision${decisions === 1 ? "" : "s"}</span>` : ""}
          ${debates ? `<span class="ep-stat">${debates} debate${debates === 1 ? "" : "s"}</span>` : ""}
        </div>
        ${agentChips ? `<div class="ep-agents">who worked: ${agentChips}</div>` : ""}
      </div>
      ${actionBanner}
      <div class="ep-tasks">${taskCards || `<div class="empty">no tasks.</div>`}</div>
      <div class="ep-raw-wrap">
        <button type="button" class="ep-raw-toggle">[ ⋯ raw json ]</button>
        <pre class="ep-raw" hidden></pre>
      </div>
    </div>`;
}

async function renderEmptyState(list) {
  // First-time founder lands on the dashboard right after First Move
  // and sees "no episodes yet" — meaningless if they just activated a
  // directive. Surface the active project + the fact that the team is
  // working on it so they know SOMETHING is happening, not that the
  // app forgot what they picked.
  list.innerHTML = `<div class="empty">no episodes yet — loading active project…</div>`;
  try {
    const projects = await api.projectsWithDrafts();
    if (!projects || !projects.length) {
      list.innerHTML = `<div class="empty">no episodes yet. Type a directive below to give the team something to run.</div>`;
      return;
    }
    const actives = projects.filter((p) => p.status === "active");
    const activeRows = actives.map(activeProjectRowHTML).join("");
    const lead = actives.length
      ? `<div class="empty">team is running your first-week directive. No completed episodes yet — first one will appear below when the team checkpoints.</div>`
      : `<div class="empty">no episodes yet. Type a directive below to give the team something to run.</div>`;
    list.innerHTML = `${lead}${activeRows}${draftsStripHTML(projects)}`;
    wireActiveProjectActions(list);
    wireDraftActions(list);
  } catch (_) {
    list.innerHTML = `<div class="empty">no episodes yet.</div>`;
  }
}

// Drafts strip (#2): unpicked first-move proposals (and any future
// template/team drafts) are real LLM work staged as status='draft'
// projects. Without a home they orphan — surface them with a [ start ]
// affordance reusing the same /activate path First Move uses. Pick-only
// for now: abandoning drafts is #10 (no engine op yet — out of scope).
function draftsStripHTML(projects) {
  const drafts = (projects || []).filter((p) => p.status === "draft");
  if (!drafts.length) return "";
  const rows = drafts.map((p) => `
    <div class="episode-item active-project draft-project" data-pid="${escapeHTML(p.id)}">
      <div class="ap-line">
        <span>▹ ${escapeHTML(p.name || "(unnamed)")}</span>
        <button type="button" class="draft-start" data-start-pid="${escapeHTML(p.id)}">[ ▶ start ]</button>
      </div>
      <div class="pid">${escapeHTML(p.id)} :: team-proposed, not started</div>
    </div>`).join("");
  return `<div class="ep-draft-wrap"><div class="ep-active-head">// DRAFTS — proposed by the team, waiting on you</div>${rows}</div>`;
}

function wireDraftActions(container) {
  for (const btn of container.querySelectorAll(".draft-start[data-start-pid]")) {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const pid = btn.getAttribute("data-start-pid");
      btn.disabled = true;
      btn.textContent = "[ ▶ starting… ]";
      try {
        await api.activateProject(pid);
        const eps = await api.episodes();
        renderEpisodes(eps || []);
      } catch (err) {
        btn.disabled = false;
        btn.textContent = "[ ▶ start ]";
      }
    });
  }
}

// One active-project row with an abandon (cancel) action. Used both in
// the empty-state and prepended to the episode list so the founder can
// always see + stop a running plan (#10).
function activeProjectRowHTML(p) {
  return `
    <div class="episode-item active-project" data-pid="${escapeHTML(p.id)}">
      <div class="ap-line">
        <span>▸ ${escapeHTML(p.name || "(unnamed)")}</span>
        <button type="button" class="ap-cancel" data-cancel-pid="${escapeHTML(p.id)}">[ ✕ abandon ]</button>
      </div>
      <div class="pid">${escapeHTML(p.id)} :: <span class="active-pulse">team working</span></div>
    </div>`;
}

function wireActiveProjectActions(container) {
  for (const btn of container.querySelectorAll(".ap-cancel[data-cancel-pid]")) {
    let armed = false;
    let t = null;
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const pid = btn.getAttribute("data-cancel-pid");
      if (!armed) {
        // Two-stage confirm (no native dialog — Tauri WebView blocks it).
        armed = true;
        btn.textContent = "[ ✕ click again to abandon ]";
        btn.classList.add("ap-cancel-armed");
        t = setTimeout(() => {
          armed = false;
          btn.textContent = "[ ✕ abandon ]";
          btn.classList.remove("ap-cancel-armed");
        }, 5000);
        return;
      }
      clearTimeout(t);
      btn.disabled = true;
      btn.textContent = "[ ✕ abandoning… ]";
      try {
        await fetch(`/projects/${encodeURIComponent(pid)}/cancel`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ reason: "abandoned from dashboard" }),
        });
        const eps = await api.episodes();
        renderEpisodes(eps || []);
      } catch (_) {
        btn.disabled = false;
        btn.textContent = "[ ✕ abandon ]";
      }
    });
  }
}

// #22: episodes are the deep record (completed projects only), but an
// empty episode list does NOT mean an agent never worked — delivered
// tasks on a still-open project are real work. Render the task-history
// fallback instead of a false "no recorded work" when the summary has
// rows for this agent. Returns true when it rendered something.
async function renderWorkSummaryFallback(host, key) {
  try {
    const summary = await api.agentsWorkSummary();
    const row = summary && summary[key];
    if (!row || !row.total) return false;
    const parts = [];
    if (row.delivered) parts.push(`${row.delivered} delivered`);
    if (row.completed) parts.push(`${row.completed} completed`);
    if (row.failed) parts.push(`${row.failed} failed`);
    const counts = parts.length ? parts.join(", ") : `${row.total} task(s)`;
    const last = row.last_active ? ` Last active ${escapeHTML(row.last_active)}.` : "";
    host.innerHTML = `<div class="empty">No completed episodes yet — but ${escapeHTML(roleLabel(key))} has ${escapeHTML(counts)} task(s) on open projects.${last} Episodes appear when a project closes.</div>`;
    return true;
  } catch (_) {
    return false;
  }
}

// Show one agent's tasks from the most recent episode, in the payload
// panel. Wired to staff-panel clicks so the founder can drill into
// "what did the CRO actually do?".
export async function showAgentTasks(role) {
  const host = document.getElementById("episodes-payload");
  if (!host || !role) return;
  const key = String(role).toLowerCase();
  host.innerHTML = `<div class="empty">loading ${escapeHTML(roleLabel(key))}'s work…</div>`;
  try {
    const eps = await api.episodes();
    if (!eps || !eps.length) {
      // Truthful empty state (#22): only claim "no recorded work" when
      // the task history is ALSO empty for this agent.
      if (await renderWorkSummaryFallback(host, key)) return;
      host.innerHTML = `<div class="empty">${escapeHTML(roleLabel(key))} has no recorded work yet — no completed episodes.</div>`;
      return;
    }
    // Newest episode first.
    const latest = eps[0];
    const row = await api.episode(latest.project_id);
    let payload = row.payload_json || row.payload || row;
    if (typeof payload === "string") { try { payload = JSON.parse(payload); } catch (_) {} }
    const tasks = (payload.tasks || []).filter(
      (t) => String(t.assigned_agent || "").toLowerCase() === key,
    );
    if (!tasks.length) {
      if (await renderWorkSummaryFallback(host, key)) return;
      host.innerHTML = `<div class="empty">${escapeHTML(roleLabel(key))} didn't own a task in the latest episode.</div>`;
      return;
    }
    const cards = tasks.map((t, i) => {
      const out = taskOutput(t.result);
      const statusCls = t.status === "completed" ? "ok" : (t.status === "failed" ? "err" : "warn");
      return `
        <div class="ep-task open">
          <div class="ep-task-head">
            <span class="ep-task-ix">${i + 1}</span>
            <span class="ep-task-title">${escapeHTML(t.title || "(untitled)")}</span>
            <span class="ep-task-status ${statusCls}">${escapeHTML(t.status || "?")}</span>
          </div>
          <div class="ep-task-body">
            ${out ? `<div class="ep-task-output">${renderText(out)}</div>` : `<div class="empty">no output.</div>`}
          </div>
        </div>`;
    }).join("");
    host.innerHTML = `
      <div class="ep-detail">
        <div class="ep-detail-head">
          <div class="ep-detail-name">${escapeHTML(roleLabel(key))} — work in "${escapeHTML(latest.summary || latest.project_id)}"</div>
        </div>
        <div class="ep-tasks">${cards}</div>
      </div>`;
  } catch (e) {
    host.innerHTML = `<div class="empty">load failed: ${escapeHTML(e.message)}</div>`;
  }
}

export function renderEpisodes(rows) {
  const list = document.getElementById("episodes-list");
  if (!list) return;
  if (!rows || !rows.length) {
    renderEmptyState(list);
    return;
  }
  list.innerHTML = rows.map((r) => {
    const isActive = r.project_id === _activeId;
    return `<div class="episode-item ${isActive ? "active" : ""}" data-pid="${escapeHTML(r.project_id)}">
      <div>${escapeHTML(r.summary || r.project_id || "(unnamed)")}</div>
      <div class="pid">${escapeHTML(r.project_id)} :: ${escapeHTML(r.retention_tier || "full")}</div>
    </div>`;
  }).join("");

  for (const item of list.querySelectorAll(".episode-item:not(.active-project)")) {
    item.addEventListener("click", () => {
      const pid = item.getAttribute("data-pid");
      _activeId = pid;
      for (const el of list.querySelectorAll(".episode-item")) el.classList.remove("active");
      item.classList.add("active");
      loadPayload(pid);
    });
  }

  // Also surface any RUNNING project at the top with an abandon action,
  // so the founder can stop a plan even once completed episodes exist
  // (#10), plus the DRAFTS strip (#2) so unpicked team proposals stay
  // reachable. Best-effort fetch; failure just omits the rows.
  api.projectsWithDrafts()
    .then((projects) => {
      const actives = (projects || []).filter((p) => p.status === "active");
      const draftsHTML = draftsStripHTML(projects);
      if (!actives.length && !draftsHTML) return;
      const wrap = document.createElement("div");
      wrap.className = "ep-active-wrap";
      const activesHTML = actives.length
        ? `<div class="ep-active-head">// RUNNING NOW</div>` +
          actives.map(activeProjectRowHTML).join("")
        : "";
      wrap.innerHTML = activesHTML + draftsHTML;
      list.prepend(wrap);
      wireActiveProjectActions(wrap);
      wireDraftActions(wrap);
    })
    .catch(() => {});
}
