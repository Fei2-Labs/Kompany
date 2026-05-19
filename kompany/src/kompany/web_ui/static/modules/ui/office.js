// Render the 11-agent C-suite panel.

const ASCII_BUSY = "◆◇◆";   // ◆◇◆
const ASCII_IDLE = "○○○";   // ○○○
const ASCII_ALERT = "◆◇◆";  // ◆◇◆ (red via CSS class)

function statusClass(status) {
  const s = (status || "").toLowerCase();
  if (s === "busy" || s === "running") return "busy";
  if (s === "awaiting" || s === "awaiting_user" || s === "alert" || s === "blocked") return "awaiting";
  return "";
}

function ascii(status) {
  const s = (status || "").toLowerCase();
  if (s === "busy" || s === "running") return ASCII_BUSY;
  if (s === "awaiting" || s === "awaiting_user" || s === "alert" || s === "blocked") return ASCII_ALERT;
  return ASCII_IDLE;
}

function statusText(row) {
  const s = (row.status || "idle").toLowerCase();
  if (s === "awaiting" || s === "awaiting_user" || s === "blocked") return "AWAIT YOU";
  if (s === "busy" || s === "running") return row.last_action ? row.last_action.slice(0, 30) : "working";
  if (s === "alert") return "ALERT";
  return "idle";
}

function latency(row) {
  if (row.latency_ms != null) {
    return `${(row.latency_ms / 1000).toFixed(1)}s`;
  }
  const s = (row.status || "").toLowerCase();
  if (s === "awaiting" || s === "awaiting_user" || s === "alert" || s === "blocked") return "!!!";
  if (s === "busy" || s === "running") return "...";
  return "--";
}

function escapeHTML(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export function renderOffice(rows) {
  const list = document.getElementById("agents-list");
  if (!list) return;
  if (!rows || !rows.length) {
    list.innerHTML = `<div class="agent-row"><span class="id">--</span><span class="ascii">...</span><span class="status-text">no agents</span><span class="latency">--</span></div>`;
    return;
  }
  list.innerHTML = rows.map((r) => {
    const cls = statusClass(r.status);
    return `<div class="agent-row ${cls}">
      <span class="id">${escapeHTML(r.role)}</span>
      <span class="ascii">${ascii(r.status)}</span>
      <span class="status-text">${escapeHTML(statusText(r))}</span>
      <span class="latency ${cls}">${escapeHTML(latency(r))}</span>
    </div>`;
  }).join("");
}
