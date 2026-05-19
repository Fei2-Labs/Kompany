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

export function renderEpisodes(rows) {
  const list = document.getElementById("episodes-list");
  if (!list) return;
  if (!rows || !rows.length) {
    list.innerHTML = `<div class="empty">no episodes yet.</div>`;
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
