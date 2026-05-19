// Live timeline panel. Keeps a rolling buffer of the latest 100 lines.

const MAX_LINES = 100;
let _entries = [];

function escapeHTML(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTime() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function render() {
  const el = document.getElementById("timeline");
  if (!el) return;
  const last = _entries.length - 1;
  el.innerHTML = _entries.map((e, i) => {
    const cls = i === last ? `line ${e.level} last` : `line ${e.level}`;
    return `<div class="${cls}">${escapeHTML(fmtTime())} ${escapeHTML(e.text)}</div>`;
  }).join("");
  el.scrollTop = el.scrollHeight;
}

export function initTimeline() {
  _entries = [{ level: "ok", text: "ready." }];
  render();
}

export function pushTimeline(entry) {
  _entries.push({ level: entry.level || "ok", text: entry.text || "" });
  if (_entries.length > MAX_LINES) {
    _entries = _entries.slice(_entries.length - MAX_LINES);
  }
  render();
}
