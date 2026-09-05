// Render the inbox: pending approval cards with action buttons.

import { api } from "/ui/static/modules/api.js?v=3";
import { buildNeedsYouItems, healthSeverity } from "/ui/static/modules/ui/needs_you.js?v=1";
import { store } from "/ui/static/modules/store.js?v=2";

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

// Inline error line (no native dialogs — Tauri WebView blocks native dialogs).
function showRowError(card, msg) {
  let el = card.querySelector(".inbox-inline-error");
  if (!el) {
    el = document.createElement("div");
    el.className = "body-line inbox-inline-error";
    el.style.color = "var(--danger, #e05555)";
    card.appendChild(el);
  }
  el.textContent = `› ${msg}`;
}

// First tap reveals an inline input row inside the card; second tap
// (the [ ok ] button, or Enter) submits. No native dialogs — the
// Kompany Tauri WebView silently returns falsy for those.
function openInlineForm(card, btn, opts) {
  // Only one inline form open per card at a time.
  card.querySelector(".inbox-inline-form")?.remove();
  if (btn.dataset.formOpen === "1") {
    delete btn.dataset.formOpen;
    return;
  }
  for (const b of card.querySelectorAll(".actions .key")) delete b.dataset.formOpen;
  btn.dataset.formOpen = "1";

  const form = document.createElement("div");
  form.className = "inbox-inline-form";
  const inputType = opts.type === "number" ? "number" : "text";
  form.innerHTML = `
    <input type="${inputType}" class="inline-input"
      placeholder="${escapeHTML(opts.placeholder || "")}"
      ${opts.value != null ? `value="${escapeHTML(String(opts.value))}"` : ""}
      ${inputType === "number" ? 'min="1" step="1"' : ""}>
    <button type="button" class="key inline-ok">[ ok ]</button>
    <button type="button" class="key inline-cancel">[ cancel ]</button>`;
  card.appendChild(form);

  const input = form.querySelector(".inline-input");
  const close = () => {
    form.remove();
    delete btn.dataset.formOpen;
  };
  const submit = async () => {
    const raw = input.value.trim();
    const okBtn = form.querySelector(".inline-ok");
    okBtn.disabled = true;
    try {
      await opts.onSubmit(raw);
      close();
      reload();
    } catch (e) {
      okBtn.disabled = false;
      showRowError(card, `${opts.label} failed: ${e.message}`);
    }
  };
  form.querySelector(".inline-ok").addEventListener("click", submit);
  form.querySelector(".inline-cancel").addEventListener("click", close);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
    if (e.key === "Escape") close();
  });
  input.focus();
}

function bindRow(card, row) {
  const id = row.id;
  card.querySelector('[data-action="approve"]')?.addEventListener("click", async () => {
    try {
      await api.approve(id);
      reload();
    } catch (e) { showRowError(card, `approve failed: ${e.message}`); }
  });
  card.querySelector('[data-action="reject"]')?.addEventListener("click", (e) => {
    openInlineForm(card, e.currentTarget, {
      label: "reject",
      placeholder: "reject reason?",
      onSubmit: (raw) => api.reject(id, raw || "rejected via ui"),
    });
  });
  card.querySelector('[data-action="revise"]')?.addEventListener("click", (e) => {
    openInlineForm(card, e.currentTarget, {
      label: "revise",
      placeholder: "counter-proposal:",
      onSubmit: (raw) => {
        if (!raw) throw new Error("counter-proposal required");
        return api.revise(id, raw);
      },
    });
  });
  card.querySelector('[data-action="snooze"]')?.addEventListener("click", (e) => {
    openInlineForm(card, e.currentTarget, {
      label: "snooze",
      type: "number",
      placeholder: "snooze minutes:",
      value: 30,
      onSubmit: (raw) => {
        const m = parseInt(raw, 10);
        if (!m || m <= 0) throw new Error("minutes must be > 0");
        return api.snooze(id, m);
      },
    });
  });
  card.querySelector('[data-action="comment"]')?.addEventListener("click", (e) => {
    openInlineForm(card, e.currentTarget, {
      label: "comment",
      placeholder: "comment:",
      onSubmit: (raw) => {
        if (!raw) throw new Error("comment required");
        return api.comment(id, raw);
      },
    });
  });
  card.querySelector('[data-action="cancel"]')?.addEventListener("click", (e) => {
    openInlineForm(card, e.currentTarget, {
      label: "cancel",
      placeholder: "cancel reason?",
      onSubmit: (raw) => api.cancel(id, raw || "cancelled via ui"),
    });
  });
}

function healthCardHTML(ev) {
  const sev = healthSeverity(ev.kind);
  const idShort = (ev.id || "").replace(/^he_/, "").slice(0, 4);
  const detail = ev.detail_json
    ? (() => { try { return JSON.parse(ev.detail_json); } catch (_) { return null; } })()
    : (ev.detail || null);
  const bits = [];
  if (detail && typeof detail === "object") {
    for (const key of ["project_id", "task_id", "agent_role", "reason"]) {
      if (detail[key]) bits.push(`${key}=${detail[key]}`);
    }
  }
  const detailLine = bits.length ? `<div class="body-line dim">› ${escapeHTML(bits.join(" · "))}</div>` : "";
  return `<div class="inbox-entry health-entry ${severityClass(sev)}" data-health-id="${escapeHTML(ev.id)}">
    <div class="id-line">[#${escapeHTML(idShort)}] WATCHDOG :: ${escapeHTML(ev.kind)} :: severity=${escapeHTML(sev)}</div>
    <div class="body-line">${escapeHTML(detail?.message || detail?.summary || ev.kind || "")}</div>
    ${detailLine}
    <div class="actions">
      <button class="key" data-health-action="continue">[c] continue</button>
      <button class="key" data-health-action="snooze">[s] snooze</button>
      <button class="key danger" data-health-action="dismiss">[x] dismiss</button>
    </div>
  </div>`;
}

function blockedCardHTML(t) {
  const role = String(t.assigned_agent || "team").toUpperCase();
  return `<div class="inbox-entry blocked-entry severity-high" data-blocked-task="${escapeHTML(t.task_id)}" data-blocked-role="${escapeHTML(t.assigned_agent || "")}">
    <div class="id-line">[BLOCKED] ${escapeHTML(role)} :: ${escapeHTML(t.project_name || t.project_id || "")}</div>
    <div class="body-line">${escapeHTML(t.title)}</div>
    <div class="body-line dim">› ${escapeHTML(t.founder_action)}</div>
    <div class="actions">
      ${t.assigned_agent ? `<button class="key" data-blocked-view>[v] view live</button>` : ""}
    </div>
  </div>`;
}

export function renderInbox(rows) {
  const list = document.getElementById("inbox-list");
  const frame = document.getElementById("inbox-frame");
  if (!list) return;

  const pending = (rows || []).filter((r) => {
    const s = (r.status || "").toLowerCase();
    return s === "pending" || s === "snoozed";
  });

  // Merge approvals + open health events + BLOCKED tasks into one
  // severity-sorted NEEDS YOU list (pure logic in needs_you.js).
  const items = buildNeedsYouItems({
    pending,
    health: store.state.health || [],
    blocked: store.state.blocked || [],
  });

  if (frame) {
    frame.setAttribute("data-label", `NEEDS YOU [${items.length}]`);
  }

  if (!items.length) {
    list.innerHTML = `<div class="empty">nothing needs you.</div>`;
    return;
  }

  list.innerHTML = items.map((item) => {
    if (item.kind === "approval") {
      const r = item.row;
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
    }
    if (item.kind === "health") return healthCardHTML(item.row);
    return blockedCardHTML(item.row);
  }).join("");

  // Wire buttons.
  for (const card of list.querySelectorAll(".inbox-entry")) {
    const id = card.getAttribute("data-id");
    if (id) {
      const row = pending.find((r) => r.id === id);
      if (row) bindRow(card, row);
      continue;
    }
    const healthId = card.getAttribute("data-health-id");
    if (healthId) {
      for (const btn of card.querySelectorAll("[data-health-action]")) {
        const action = btn.getAttribute("data-health-action");
        btn.addEventListener("click", (e) => {
          if (action === "snooze") {
            openInlineForm(card, e.currentTarget, {
              label: "snooze",
              type: "number",
              placeholder: "snooze minutes:",
              value: 30,
              onSubmit: (raw) => {
                const m = parseInt(raw, 10);
                if (!m || m <= 0) throw new Error("minutes must be > 0");
                return api.resolveHealth(healthId, "snooze", m);
              },
            });
            return;
          }
          btn.disabled = true;
          api.resolveHealth(healthId, action)
            .then(() => reload())
            .catch((err) => { btn.disabled = false; showRowError(card, `resolve failed: ${err.message}`); });
        });
      }
      continue;
    }
    const blockedRole = card.getAttribute("data-blocked-role");
    if (blockedRole) {
      card.querySelector("[data-blocked-view]")?.addEventListener("click", () => {
        document.dispatchEvent(new CustomEvent("agent-click", { detail: { role: blockedRole } }));
      });
    }
  }
}
