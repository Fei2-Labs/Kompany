// Right drawer-ish episode list + payload viewer.

import { api } from "/ui/static/modules/api.js";

function escapeHTML(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

let _activeId = null;

async function loadPayload(projectId) {
  const pre = document.getElementById("episodes-payload");
  if (!pre) return;
  pre.innerHTML = `<pre>loading ${escapeHTML(projectId)}...</pre>`;
  try {
    const row = await api.episode(projectId);
    let payload = row.payload_json || row.payload || row;
    if (typeof payload === "string") {
      try { payload = JSON.parse(payload); } catch (_) {}
    }
    pre.innerHTML = `<pre>${escapeHTML(JSON.stringify(payload, null, 2))}</pre>`;
  } catch (e) {
    pre.innerHTML = `<pre class="empty">load failed: ${escapeHTML(e.message)}</pre>`;
  }
}

async function renderEmptyState(list) {
  // First-time founder lands on the dashboard right after First Move
  // and sees "no episodes yet" — meaningless if they just activated a
  // directive. Surface the active project + the fact that the team is
  // working on it so they know SOMETHING is happening, not that the
  // app forgot what they picked.
  list.innerHTML = `<div class="empty">no episodes yet — loading active project…</div>`;
  try {
    const projects = await fetch("/projects", {
      headers: { Accept: "application/json" },
    }).then((r) => (r.ok ? r.json() : []));
    if (!projects || !projects.length) {
      list.innerHTML = `<div class="empty">no episodes yet. Type a directive below to give the team something to run.</div>`;
      return;
    }
    const activeRows = projects
      .filter((p) => p.status === "active")
      .map((p) => `
        <div class="episode-item active-project" data-pid="${escapeHTML(p.id)}">
          <div>▸ ${escapeHTML(p.name || "(unnamed)")}</div>
          <div class="pid">${escapeHTML(p.id)} :: <span class="active-pulse">team working</span></div>
        </div>`)
      .join("");
    list.innerHTML = `
      <div class="empty">team is running your first-week directive. No completed episodes yet — first one will appear below when the team checkpoints.</div>
      ${activeRows}
    `;
  } catch (_) {
    list.innerHTML = `<div class="empty">no episodes yet.</div>`;
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

  for (const item of list.querySelectorAll(".episode-item")) {
    item.addEventListener("click", () => {
      const pid = item.getAttribute("data-pid");
      _activeId = pid;
      for (const el of list.querySelectorAll(".episode-item")) el.classList.remove("active");
      item.classList.add("active");
      loadPayload(pid);
    });
  }
}
