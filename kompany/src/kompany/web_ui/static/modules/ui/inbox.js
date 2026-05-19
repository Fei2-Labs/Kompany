// Render the inbox: pending approval cards with action buttons.

import { api } from "/ui/static/modules/api.js";
import { store } from "/ui/static/modules/store.js";

function escapeHTML(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function severityClass(sev) {
  const s = (sev || "").toLowerCase();
  if (s === "low") return "severity-low";
  if (s === "medium") return "severity-medium";
  return "severity-high";
}

// For glossary_review approvals, render the per-drift excerpts inline so
// the founder sees exactly which agent used what forbidden synonym.
function renderGlossaryDrifts(row) {
  if (row.action_type !== "glossary_review") return "";
  const payload = row.payload || {};
  const drifts = Array.isArray(payload.drifts) ? payload.drifts : [];
  if (!drifts.length) return "";
  const lines = drifts.slice(0, 6).map((d) => {
    const term = escapeHTML(d.term || "");
    const syn = escapeHTML(d.drifted_synonym || "");
    const agent = escapeHTML((d.agent_role || "?").toUpperCase());
    const count = d.count != null ? `×${d.count}` : "";
    const excerpt = d.sample_excerpt
      ? `<span class="drift-excerpt">${escapeHTML(d.sample_excerpt).slice(0, 200)}</span>`
      : "";
    return `<div class="body-line drift-line">› ${agent} used <b>${syn}</b> ${count} — canonical: <b>${term}</b>${excerpt ? "<br>" + excerpt : ""}</div>`;
  });
  const more = drifts.length > 6 ? `<div class="body-line dim">… ${drifts.length - 6} more drift(s)</div>` : "";
  return lines.join("") + more;
}

async function reload() {
  try {
    const rows = await api.inbox();
    store.update("inbox", rows);
  } catch (e) {
    console.warn("inbox reload failed", e);
  }
}

function bindRow(card, row) {
  const id = row.id;
  card.querySelector('[data-action="approve"]')?.addEventListener("click", async () => {
    try {
      await api.approve(id);
      reload();
    } catch (e) { alert(`approve failed: ${e.message}`); }
  });
  card.querySelector('[data-action="reject"]')?.addEventListener("click", async () => {
    const reason = prompt("reject reason?") || "rejected via ui";
    try {
      await api.reject(id, reason);
      reload();
    } catch (e) { alert(`reject failed: ${e.message}`); }
  });
  card.querySelector('[data-action="revise"]')?.addEventListener("click", async () => {
    const counter = prompt("counter-proposal:");
    if (!counter) return;
    try {
      await api.revise(id, counter);
      reload();
    } catch (e) { alert(`revise failed: ${e.message}`); }
  });
  card.querySelector('[data-action="snooze"]')?.addEventListener("click", async () => {
    const m = parseInt(prompt("snooze minutes:", "30"), 10);
    if (!m || m <= 0) return;
    try {
      await api.snooze(id, m);
      reload();
    } catch (e) { alert(`snooze failed: ${e.message}`); }
  });
  card.querySelector('[data-action="comment"]')?.addEventListener("click", async () => {
    const body = prompt("comment:");
    if (!body) return;
    try {
      await api.comment(id, body);
      reload();
    } catch (e) { alert(`comment failed: ${e.message}`); }
  });
  card.querySelector('[data-action="cancel"]')?.addEventListener("click", async () => {
    const reason = prompt("cancel reason?") || "cancelled via ui";
    try {
      await api.cancel(id, reason);
      reload();
    } catch (e) { alert(`cancel failed: ${e.message}`); }
  });
}

export function renderInbox(rows) {
  const list = document.getElementById("inbox-list");
  const frame = document.getElementById("inbox-frame");
  if (!list) return;

  const pending = (rows || []).filter((r) => {
    const s = (r.status || "").toLowerCase();
    return s === "pending" || s === "snoozed";
  });

  if (frame) {
    frame.setAttribute("data-label", `INBOX [${pending.length} pending]`);
  }

  if (!pending.length) {
    list.innerHTML = `<div class="empty">inbox is empty.</div>`;
    return;
  }

  list.innerHTML = pending.map((r) => {
    const sevCls = severityClass(r.severity);
    const requestedBy = (r.requested_by || "?").toUpperCase();
    const idShort = (r.id || "").slice(0, 4);
    return `<div class="inbox-entry ${sevCls}" data-id="${escapeHTML(r.id)}">
      <div class="id-line">[#${escapeHTML(idShort)}] ${escapeHTML(requestedBy)} :: ${escapeHTML(r.action_type)} :: severity=${escapeHTML(r.severity || "high")}</div>
      <div class="body-line">${escapeHTML(r.summary || "")}</div>
      ${r.status === "snoozed" ? `<div class="body-line">› snoozed until ${escapeHTML(r.snoozed_until || "")}</div>` : ""}
      ${renderGlossaryDrifts(r)}
      <div class="actions">
        <button class="key" data-action="approve">[y] approve</button>
        <button class="key danger" data-action="reject">[n] reject</button>
        <button class="key" data-action="revise">[r] revise</button>
        <button class="key" data-action="snooze">[s] snooze</button>
        <button class="key" data-action="comment">[c] comment</button>
        <button class="key danger" data-action="cancel">[x] cancel</button>
      </div>
    </div>`;
  }).join("");

  // Wire buttons.
  for (const card of list.querySelectorAll(".inbox-entry")) {
    const id = card.getAttribute("data-id");
    const row = pending.find((r) => r.id === id);
    if (row) bindRow(card, row);
  }
}
