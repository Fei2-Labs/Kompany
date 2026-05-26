// Onboard v2 — multi-step cyberpunk wizard.
//
// State machine driving 7 ordered steps (PRD 05-19-onboard-v2-flow):
//   0. boot          — typed-out terminal sequence, first-boot only
//   1. connection    — provider + API key + explicit TEST UPLINK
//   2. faction       — 4-card 2x2 grid + blank escape + 2 niche fold
//   3. mission       — budget/revenue/customer/deadline + glossary edit
//                       + right-column engine preview
//   4. review        — delegates to feasibility_review.js (sibling task)
//   5. first_move    — 3 staged directives → pick + activate
//   6. provisioning  — real subsystem status lines + ledger pivot
//
// The draft (everything except api_key) persists to localStorage on
// every state change so a tab close / reload pops the user back exactly
// where they were. The api_key is wiped on reload by design.
//
// Faction display aliases per [[glossary-canonical-terms]]:
//   BUILDER  → saas-startup
//   OPERATOR → consulting-firm
//   TRADER   → ecommerce
//   BARD     → content-creator

import { mountFeasibilityReview } from "./feasibility_review.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DRAFT_KEY = "kompany_onboard_draft_v1";
const FIRST_BOOT_KEY = "kompany_first_boot_done_v1";
const DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const DRAFT_VERSION = 1;

const STEP_ORDER = [
  "boot",
  "connection",
  "faction",
  "mission",
  "review",
  "first_move",
  "provisioning",
];

const STEP_LABELS = {
  boot: "FIRST-BOOT",
  connection: "CONNECTION",
  faction: "FACTION",
  mission: "MISSION-BRIEFING",
  review: "TEAM-REVIEW",
  first_move: "FIRST-MOVE",
  provisioning: "PROVISIONING",
};

// Faction display alias → template_id.
const FACTIONS = [
  {
    alias: "BUILDER",
    template_id: "saas-startup",
    tagline: "Ship software. Recurring revenue.",
  },
  {
    alias: "OPERATOR",
    template_id: "consulting-firm",
    tagline: "Sell time. Senior expertise.",
  },
  {
    alias: "TRADER",
    template_id: "ecommerce",
    tagline: "Move product. DTC storefront.",
  },
  {
    alias: "BARD",
    template_id: "content-creator",
    tagline: "Grow audience. Monetize attention.",
  },
];

// Niche templates folded under [▾ MORE STRATEGIES (2)].
const NICHE_TEMPLATES = ["indie-tool", "community"];

// Per-faction weekly burn-cap heuristic (used by Mission Briefing's
// engine preview). Tuned to match the templates' implied tempo; the
// runway warning fires when initial_budget / burn < 4 weeks.
const WEEKLY_BURN_CAP = {
  "saas-startup": 220,
  "consulting-firm": 80,
  "ecommerce": 180,
  "content-creator": 60,
  "indie-tool": 50,
  "community": 40,
  "blank": 100,
};

// ---------------------------------------------------------------------------
// DOM handles
// ---------------------------------------------------------------------------

const stepHost = document.getElementById("onb-step");
const stepLabelEl = document.getElementById("onb-step-label");
const stepCountEl = document.getElementById("onb-step-count");
const statusEl = document.getElementById("onb-status");
const errorBox = document.getElementById("onb-error");
const resumeBanner = document.getElementById("onb-resume");
const resumeMetaEl = document.getElementById("onb-resume-meta");
const resumeYesBtn = document.getElementById("onb-resume-yes");
const resumeNoBtn = document.getElementById("onb-resume-no");

// ---------------------------------------------------------------------------
// Wizard state (in-memory)
// ---------------------------------------------------------------------------

// ``data`` is the founder's draft — everything we persist except the
// api_key. ``api_key`` lives on the wizard instance only.
const state = {
  step: "boot",
  api_key: "",
  ping: null, // { ok, model, pricing } once test uplink succeeds
  templateManifests: {}, // id -> manifest snapshot (cache for /templates)
  approval: null, // feasibility review approval payload
  approval_skipped: false,
  draft_project_ids: [], // populated from /projects after onboarding complete
  data: {
    provider: "anthropic",
    base_url: "",
    template_id: "saas-startup",
    initial_budget: null,
    revenue_target: null,
    customer_target: null,
    deadline: null,
    directive: null,
    glossary_overrides: {},
    first_directive_project_id: null,
  },
};

// ---------------------------------------------------------------------------
// Draft persistence
// ---------------------------------------------------------------------------

function saveDraft() {
  try {
    const payload = {
      version: DRAFT_VERSION,
      saved_at: new Date().toISOString(),
      step: state.step,
      data: state.data,
    };
    localStorage.setItem(DRAFT_KEY, JSON.stringify(payload));
  } catch (_) {
    // Quota / disabled storage — non-fatal.
  }
}

function loadDraft() {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.version !== DRAFT_VERSION) return null;
    return parsed;
  } catch (_) {
    return null;
  }
}

function dropDraft() {
  try {
    localStorage.removeItem(DRAFT_KEY);
  } catch (_) {
    /* noop */
  }
}

function draftIsExpired(draft) {
  if (!draft || !draft.saved_at) return false;
  const t = Date.parse(draft.saved_at);
  if (Number.isNaN(t)) return false;
  return Date.now() - t > DRAFT_TTL_MS;
}

function formatAgo(isoString) {
  const t = Date.parse(isoString || "");
  if (Number.isNaN(t)) return "earlier";
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} hr ago`;
  return `${Math.floor(sec / 86400)} day ago`;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtUsd(n) {
  const v = Number(n || 0);
  if (Math.abs(v) >= 1000) return `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  if (Math.abs(v) >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}`;
}

function setStatus(text) {
  if (statusEl) statusEl.textContent = text;
}

function setStepHeader(step) {
  if (stepLabelEl) stepLabelEl.textContent = STEP_LABELS[step] || step.toUpperCase();
  if (stepCountEl) {
    const idx = STEP_ORDER.indexOf(step);
    stepCountEl.textContent = `${Math.max(0, idx)}/${STEP_ORDER.length - 1}`;
  }
}

function showError(msg) {
  if (!errorBox) return;
  errorBox.hidden = false;
  errorBox.textContent = "// ERROR: " + (msg || "unknown error");
  setStatus("error");
}

function clearError() {
  if (errorBox) {
    errorBox.hidden = true;
    errorBox.textContent = "";
  }
}

// Fetch a template manifest with simple memoization.
async function loadManifest(templateId) {
  if (!templateId) return null;
  if (state.templateManifests[templateId]) return state.templateManifests[templateId];
  try {
    const res = await fetch(`/templates/${encodeURIComponent(templateId)}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;
    const data = await res.json();
    state.templateManifests[templateId] = data;
    return data;
  } catch (_) {
    return null;
  }
}

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

function goto(step) {
  state.step = step;
  saveDraft();
  setStepHeader(step);
  clearError();
  render();
}

function render() {
  stepHost.innerHTML = "";
  switch (state.step) {
    case "boot":
      return renderBoot();
    case "connection":
      return renderConnection();
    case "faction":
      return renderFaction();
    case "mission":
      return renderMission();
    case "review":
      return renderReview();
    case "first_move":
      return renderFirstMove();
    case "provisioning":
      return renderProvisioning();
    default:
      return renderConnection();
  }
}

// ---------------------------------------------------------------------------
// Step 0 — Boot sequence
// ---------------------------------------------------------------------------

const BOOT_LINES_TEMPLATE = [
  { tag: "OK", label: "BIOS check", value: "KOMPANY v0.1.0" },
  { tag: "OK", label: "Sidecar PID", value: "" }, // filled from /health
  { tag: "OK", label: "Vault initialized", value: "~/.kompany/vault.db" },
  { tag: "OK", label: "Event hub online", value: "SSE stream ready" },
  { tag: "OK", label: "Capital ledger", value: "$0.00 (uninitialized)" },
  { tag: "OK", label: "Agent roster", value: "0 hired" },
  { tag: "..", label: "OPERATOR_AUTH", value: "pending" },
];

async function renderBoot() {
  setStatus("booting...");
  const wrap = document.createElement("div");
  wrap.className = "onb-boot frame";
  wrap.dataset.label = "BOOT_SEQUENCE";

  const sub = document.createElement("div");
  sub.className = "onb-boot-sub";
  sub.textContent = `build: v0.1.0 · ${navigator.platform || "unknown"}`;
  wrap.appendChild(sub);

  const list = document.createElement("pre");
  list.className = "onb-boot-lines";
  wrap.appendChild(list);

  const skip = document.createElement("button");
  skip.type = "button";
  skip.className = "onb-btn onb-btn-skip";
  skip.textContent = "[ ▸ skip ]";
  skip.addEventListener("click", () => completeBoot(true));
  wrap.appendChild(skip);

  stepHost.appendChild(wrap);

  // Resolve /health for live PID + bind addr (best effort).
  let pid = "----";
  let bindAddr = "127.0.0.1:8765";
  try {
    const healthRes = await fetch("/health", { headers: { Accept: "application/json" } });
    if (healthRes.ok) {
      const h = await healthRes.json();
      if (h.pid != null) pid = String(h.pid);
      if (h.bind) bindAddr = String(h.bind);
    }
  } catch (_) {
    /* health may be a no-op endpoint — fall through with placeholders */
  }

  const lines = BOOT_LINES_TEMPLATE.map((l) => ({ ...l }));
  for (const ln of lines) {
    if (ln.label === "Sidecar PID") ln.value = `${pid} ............. ${bindAddr}`;
  }

  // Type lines out 1 per 250ms. State could already have changed (user
  // skipped) — guard before mutating DOM.
  let cancelled = false;
  skip.addEventListener("click", () => { cancelled = true; });

  for (const ln of lines) {
    if (cancelled || state.step !== "boot") break;
    const dots = ".".repeat(Math.max(2, 26 - ln.label.length));
    list.textContent += `[${ln.tag}]  ${ln.label} ${dots} ${ln.value}\n`;
    await sleep(250);
  }
  if (cancelled || state.step !== "boot") return;
  list.textContent += "\n                  ▸ AWAITING OPERATOR INPUT █";

  await sleep(500);
  if (state.step === "boot") completeBoot(false);
}

function completeBoot(_userSkipped) {
  try {
    localStorage.setItem(FIRST_BOOT_KEY, "1");
  } catch (_) { /* noop */ }
  goto("connection");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---------------------------------------------------------------------------
// Step 1 — Connection (provider + API key + TEST UPLINK)
// ---------------------------------------------------------------------------

function renderConnection() {
  setStatus("connection");
  const frame = document.createElement("div");
  frame.className = "frame onb-conn";
  frame.dataset.label = "CONNECTION // 01.uplink";

  frame.innerHTML = `
    <div class="onb-field">
      <label for="onb-provider-button">// COGNITION_PROVIDER</label>
      <input type="hidden" id="onb-provider" value="${escapeHtml(state.data.provider)}">
      <div class="cyb-combobox" id="onb-provider-combobox" aria-expanded="false" data-target="onb-provider">
        <button type="button" id="onb-provider-button" class="cyb-combobox-button" aria-haspopup="listbox" aria-controls="onb-provider-list">${providerDisplayLabel(state.data.provider)}</button>
        <ul id="onb-provider-list" class="cyb-combobox-list" role="listbox" aria-labelledby="onb-provider-button">
          <li class="cyb-combobox-option" role="option" data-value="anthropic" ${state.data.provider==='anthropic'?'aria-selected="true"':''}>anthropic (Claude)</li>
          <li class="cyb-combobox-option" role="option" data-value="openai" ${state.data.provider==='openai'?'aria-selected="true"':''}>openai (GPT)</li>
          <li class="cyb-combobox-option" role="option" data-value="gemini" ${state.data.provider==='gemini'?'aria-selected="true"':''}>gemini (Google)</li>
          <li class="cyb-combobox-option" role="option" data-value="glm" ${state.data.provider==='glm'?'aria-selected="true"':''}>glm (Zhipu)</li>
          <li class="cyb-combobox-option" role="option" data-value="kimi" ${state.data.provider==='kimi'?'aria-selected="true"':''}>kimi (Moonshot)</li>
          <li class="cyb-combobox-option" role="option" data-value="custom" ${state.data.provider==='custom'?'aria-selected="true"':''}>custom (OpenAI-compatible)</li>
        </ul>
      </div>
    </div>

    <div class="onb-field" id="onb-base-url-field" ${state.data.provider==='custom'?'':'hidden'}>
      <label for="onb-base-url">// BASE URL</label>
      <input id="onb-base-url" type="url" autocomplete="off" spellcheck="false" placeholder="https://your-endpoint.example.com/v1" value="${escapeHtml(state.data.base_url||'')}">
      <p class="onb-hint">Required for custom providers. OpenAI-compatible Chat Completions API.</p>
    </div>

    <div class="onb-field">
      <label for="onb-api-key">// PROVIDER_LICENSE_KEY</label>
      <input id="onb-api-key" type="password" autocomplete="off" spellcheck="false" placeholder="sk-..." required>
      <p class="onb-hint">Stored encrypted in the local vault. Never sent anywhere else.</p>
    </div>

    <div class="onb-conn-actions">
      <div class="onb-conn-result" id="onb-conn-result" hidden></div>
    </div>

    <div class="onb-actions">
      <button type="button" class="onb-btn onb-btn-submit" id="onb-conn-next">[ ▸ NEXT ]</button>
    </div>
  `;
  stepHost.appendChild(frame);

  initCombobox("onb-provider-combobox");
  const providerInput = document.getElementById("onb-provider");
  const baseField = document.getElementById("onb-base-url-field");
  const baseInput = document.getElementById("onb-base-url");
  const apiKeyInput = document.getElementById("onb-api-key");
  const result = document.getElementById("onb-conn-result");
  const nextBtn = document.getElementById("onb-conn-next");
  const NEXT_LABEL = "[ ▸ NEXT ]";

  providerInput.addEventListener("change", () => {
    state.data.provider = providerInput.value;
    if (providerInput.value === "custom") baseField.hidden = false;
    else baseField.hidden = true;
    state.ping = null;
    result.hidden = true;
    saveDraft();
  });
  baseInput.addEventListener("input", () => {
    state.data.base_url = baseInput.value.trim();
    state.ping = null;
    saveDraft();
  });
  apiKeyInput.addEventListener("input", () => {
    state.api_key = apiKeyInput.value;
    state.ping = null;
    result.hidden = true;
  });

  // NEXT auto-tests the uplink and advances on success. The previous
  // explicit [ TEST UPLINK ] + [ NEXT ] split traded a click for a
  // pre-commit model-pick preview that most operators didn't read —
  // merging removes the extra click; failures still keep the founder
  // on this step with a clear error in the result panel.
  nextBtn.addEventListener("click", async () => {
    if (nextBtn.disabled) return;
    state.api_key = apiKeyInput.value;
    if (!state.api_key) {
      renderPingResult(result, { ok: false, error_code: "missing_key", error_message: "API key is required" });
      return;
    }
    if (state.data.provider === "custom" && !state.data.base_url) {
      renderPingResult(result, { ok: false, error_code: "missing_base_url", error_message: "base_url is required for custom provider" });
      return;
    }
    nextBtn.disabled = true;
    nextBtn.textContent = "[ ▸ testing uplink... ]";
    let payload;
    try {
      const res = await fetch("/onboarding/ping", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          provider: state.data.provider,
          api_key: state.api_key,
          base_url: state.data.base_url || undefined,
        }),
      });
      payload = await res.json();
    } catch (err) {
      payload = { ok: false, error_code: "network", error_message: err.message };
    }
    state.ping = payload;
    if (payload && payload.ok) {
      // Skip the brief success render — advancing IS the success signal.
      goto("faction");
      return;
    }
    renderPingResult(result, payload);
    nextBtn.disabled = false;
    nextBtn.textContent = NEXT_LABEL;
  });
}

function providerDisplayLabel(p) {
  const labels = {
    anthropic: "anthropic (Claude)",
    openai: "openai (GPT)",
    gemini: "gemini (Google)",
    glm: "glm (Zhipu)",
    kimi: "kimi (Moonshot)",
    custom: "custom (OpenAI-compatible)",
  };
  return labels[p] || p;
}

function renderPingResult(host, payload) {
  host.hidden = false;
  const tested = payload && payload.model_tested;
  const count = payload && payload.available_models ? payload.available_models.length : null;
  const testedLine = tested
    ? `<div class="onb-conn-tested">// model_tested: <b>${escapeHtml(tested)}</b>${count ? ` (1 of ${count} discovered)` : ""}</div>`
    : "";
  if (payload && payload.ok) {
    const model = payload.model || "unknown";
    const px = payload.pricing;
    const pxText = px ? ` · $${px.in_per_mtok} in / $${px.out_per_mtok} out per 1M` : "";
    host.className = "onb-conn-result onb-conn-ok";
    host.innerHTML = `✓ connected: <b>${escapeHtml(model)}</b>${escapeHtml(pxText)}${testedLine}`;
    return;
  }
  const code = (payload && payload.error_code) || "unknown";
  const msg = (payload && payload.error_message) || "unknown error";
  let hint = "";
  if (code === "unauthorized") hint = " — double-check the key or rotate a fresh one";
  else if (code === "rate_limited") hint = " — provider says slow down; try again in a minute";
  else if (code === "network") hint = " — provider unreachable from this machine";
  else if (code === "provider_error") hint = " — provider returned 5xx; not your key";
  host.className = "onb-conn-result onb-conn-err";
  host.innerHTML = `✗ <b>[${escapeHtml(code)}]</b> ${escapeHtml(msg)}${escapeHtml(hint)}${testedLine}`;
}

// ---------------------------------------------------------------------------
// Step 2 — Faction selection
// ---------------------------------------------------------------------------

async function renderFaction() {
  setStatus("faction selection");
  const frame = document.createElement("div");
  frame.className = "frame onb-faction";
  frame.dataset.label = "FACTION // 02.select";

  frame.innerHTML = `
    <div class="onb-faction-head">
      <span>// SELECT YOUR FACTION</span>
      <button type="button" class="onb-link-escape" id="onb-blank-escape">[ ▸ START FROM BLANK ]</button>
    </div>
    <div class="onb-faction-grid" id="onb-faction-grid">
      <div class="onb-loading">loading factions...</div>
    </div>
    <details class="onb-niche" id="onb-niche">
      <summary>▾ MORE STRATEGIES (2)</summary>
      <div class="onb-niche-list" id="onb-niche-list">
        <div class="onb-loading">loading...</div>
      </div>
    </details>
    <div class="onb-actions">
      <button type="button" class="onb-btn onb-btn-back" id="onb-back-faction">[ ◂ back ]</button>
    </div>
  `;
  stepHost.appendChild(frame);

  document.getElementById("onb-blank-escape").addEventListener("click", () => {
    state.data.template_id = "blank";
    state.data.initial_budget = null;
    state.data.revenue_target = null;
    state.data.customer_target = null;
    saveDraft();
    goto("mission");
  });
  document.getElementById("onb-back-faction").addEventListener("click", () => goto("connection"));

  const grid = document.getElementById("onb-faction-grid");
  const niche = document.getElementById("onb-niche-list");

  // Resolve all main faction manifests in parallel; render skeletons then
  // refine. Keep going if /templates is slow — we still get a usable card.
  const manifests = await Promise.all(
    FACTIONS.map((f) => loadManifest(f.template_id)),
  );
  grid.innerHTML = "";
  FACTIONS.forEach((fac, idx) => {
    const card = renderFactionCard(fac, manifests[idx]);
    grid.appendChild(card);
  });

  niche.innerHTML = "";
  for (const id of NICHE_TEMPLATES) {
    const m = await loadManifest(id);
    const row = document.createElement("button");
    row.type = "button";
    row.className = "onb-niche-row";
    const name = (m && m.name) || id;
    const mission = (m && m.mission_title) || "";
    row.innerHTML = `<b>${escapeHtml(name)}</b><span> · ${escapeHtml(mission)}</span>`;
    row.addEventListener("click", () => selectFaction(id));
    niche.appendChild(row);
  }
}

function renderFactionCard(fac, manifest) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "onb-faction-card";
  card.dataset.alias = fac.alias;
  card.dataset.templateId = fac.template_id;

  const budget = manifest ? Number(manifest.initial_budget || 0) : null;
  const rev = manifest ? Number(manifest.revenue_target || 0) : null;
  const cust = manifest ? manifest.customer_target : null;
  const roster = manifest && Array.isArray(manifest.enabled_agents) ? manifest.enabled_agents.length : null;
  const vocab = manifest && Array.isArray(manifest.glossary) ? manifest.glossary.map((g) => g.term) : [];

  card.innerHTML = `
    <div class="onb-fc-alias">[ THE ${escapeHtml(fac.alias)} ]</div>
    <div class="onb-fc-tagline">${escapeHtml(fac.tagline)}</div>
    <div class="onb-fc-stats">
      <div><span>budget:</span> ${budget != null ? fmtUsd(budget) : '--'}</div>
      <div><span>rev tgt:</span> ${rev != null ? fmtUsd(rev) : '--'}</div>
      <div><span>custom:</span> ${cust != null ? cust : '--'}</div>
      <div><span>roster:</span> ${roster != null ? `${roster} agents` : '--'}</div>
    </div>
    <details class="onb-fc-vocab">
      <summary>▾ vocabulary (${vocab.length})</summary>
      <div class="onb-fc-vocab-list">${vocab.map((v) => escapeHtml(v)).join(" · ")}</div>
    </details>
  `;
  card.addEventListener("click", (evt) => {
    // Don't fire selection if the founder clicked the vocab disclosure.
    if (evt.target.closest("details")) return;
    selectFaction(fac.template_id);
  });
  return card;
}

async function selectFaction(templateId) {
  state.data.template_id = templateId;
  // Reset overrides so the new template's defaults take over.
  state.data.initial_budget = null;
  state.data.revenue_target = null;
  state.data.customer_target = null;
  state.data.glossary_overrides = {};
  // Pre-warm the manifest cache.
  await loadManifest(templateId);
  saveDraft();
  goto("mission");
}

// ---------------------------------------------------------------------------
// Step 3 — Mission Briefing (left form + right engine preview)
// ---------------------------------------------------------------------------

async function renderMission() {
  setStatus("mission briefing");
  const showCustomerTarget = state.data.template_id !== "blank";
  if (!showCustomerTarget && state.data.customer_target != null) {
    state.data.customer_target = null;
    saveDraft();
  }
  const frame = document.createElement("div");
  frame.className = "frame onb-mission";
  frame.dataset.label = "MISSION_BRIEFING // 03.brief";
  const customerTargetField = showCustomerTarget
    ? `
        <div class="onb-field onb-mission-row">
          <label for="onb-customer-target">
            CUSTOMER_TARGET
            <span class="onb-field-hint">(optional, paying customers)</span>
          </label>
          <input id="onb-customer-target" type="number" min="0" step="1" autocomplete="off" placeholder="e.g. 50">
          <div class="onb-field-note">Use this only if you want a customer-count goal alongside revenue.</div>
        </div>
      `
    : "";
  // Compute stale based on snapshot diff — only show the warning when
  // the founder has actually edited a value since the last successful
  // review. Bare back-nav from review → mission shows nothing.
  const snap = state.data._submitted_snapshot;
  const hasReviewed = !!state.approval && !!snap;
  const valuesDiffer = hasReviewed && (
    Number(snap.initial_budget) !== Number(state.data.initial_budget) ||
    Number(snap.revenue_target) !== Number(state.data.revenue_target) ||
    (snap.customer_target ?? null) !== (state.data.customer_target ?? null) ||
    (snap.deadline || null) !== (state.data.deadline || null)
  );
  const stalePill = valuesDiffer
    ? `<div class="onb-stale-pill" id="onb-stale-pill">
         ⚠ You changed your numbers since the last review. Submitting will discard the previous review and re-run with the new numbers (one LLM debate, ~$0.50).
       </div>`
    : (hasReviewed
        ? `<div class="onb-stale-pill onb-stale-pill-ok" id="onb-stale-pill">
             ✓ These numbers were already reviewed by the team — submitting goes straight back to the review screen without another LLM call.
           </div>`
        : "");
  frame.innerHTML = `
    ${stalePill}
    <div class="onb-mission-grid">
      <div class="onb-mission-left">
        <div class="onb-field onb-mission-row">
          <label for="onb-budget">STARTING_BUDGET (real USD)</label>
          <input id="onb-budget" type="number" min="0" step="50" autocomplete="off">
        </div>
        <div class="onb-field onb-mission-row">
          <label for="onb-revenue-target">REVENUE_TARGET (USD)</label>
          <input id="onb-revenue-target" type="number" min="0" step="100" autocomplete="off">
        </div>
        ${customerTargetField}
        <div class="onb-field onb-mission-row">
          <label for="onb-deadline">DEADLINE (ISO date)</label>
          <div class="onb-date-row">
            <input id="onb-deadline" type="date" class="onb-date" autocomplete="off">
            <button type="button" class="onb-btn onb-date-today" id="onb-deadline-today" aria-label="Set deadline to today" title="Jump to today">[ • today ]</button>
          </div>
        </div>
        <details class="onb-glossary" id="onb-glossary">
          <summary id="onb-glossary-summary">▾ VOCABULARY OVERRIDE (0 terms)</summary>
          <div class="onb-glossary-list" id="onb-glossary-list"></div>
        </details>
      </div>
      <div class="onb-mission-right">
        <div class="onb-preview-head">// ENGINE PREVIEW</div>
        <div class="onb-preview-rows" id="onb-preview-rows"></div>
        <div class="onb-preview-warning" id="onb-preview-warning" hidden></div>
        <div class="onb-preview-head onb-preview-sub">// AUTO-DRAFTED DIRECTIVES (wk 1)</div>
        <div class="onb-preview-directives" id="onb-preview-directives"></div>
      </div>
    </div>
    <div class="onb-actions">
      <button type="button" class="onb-btn onb-btn-back" id="onb-back-mission">[ ◂ back ]</button>
      <button type="button" class="onb-btn onb-btn-submit" id="onb-submit-mission">[ ▸ SUBMIT TO TEAM ]</button>
    </div>
  `;
  stepHost.appendChild(frame);

  document.getElementById("onb-back-mission").addEventListener("click", () => {
    if (state.data.template_id === "blank") goto("faction");
    else goto("faction");
  });

  const manifest = await loadManifest(state.data.template_id);
  const tplBudget = manifest ? Number(manifest.initial_budget || 0) : 0;
  const tplRev = manifest ? Number(manifest.revenue_target || 0) : 0;
  const tplCust = manifest && manifest.customer_target != null ? Number(manifest.customer_target) : null;

  const budgetEl = document.getElementById("onb-budget");
  const revEl = document.getElementById("onb-revenue-target");
  const custEl = document.getElementById("onb-customer-target");
  const dlEl = document.getElementById("onb-deadline");
  const todayBtn = document.getElementById("onb-deadline-today");

  budgetEl.placeholder = tplBudget ? `${tplBudget} (template default)` : "5000";
  revEl.placeholder = tplRev ? `${tplRev} (template default)` : "10000";
  if (custEl) {
    custEl.placeholder = tplCust != null ? `${tplCust} (template default)` : "e.g. 50";
  }

  // Re-populate from saved draft (if exists) or fall back to template default.
  budgetEl.value = state.data.initial_budget != null ? String(state.data.initial_budget) : "";
  revEl.value = state.data.revenue_target != null ? String(state.data.revenue_target) : "";
  if (custEl) {
    custEl.value = state.data.customer_target != null ? String(state.data.customer_target) : "";
  }
  dlEl.value = state.data.deadline || defaultDeadline();
  todayBtn.addEventListener("click", () => {
    dlEl.value = formatLocalISODate(new Date());
    state.data.deadline = dlEl.value;
    saveDraft();
    refreshPreview(manifest);
  });

  function onChange() {
    state.data.initial_budget = budgetEl.value ? Number(budgetEl.value) : null;
    state.data.revenue_target = revEl.value ? Number(revEl.value) : null;
    if (custEl) {
      state.data.customer_target = custEl.value ? Number(custEl.value) : null;
    }
    state.data.deadline = dlEl.value || null;
    saveDraft();
    refreshPreview(manifest);
  }
  budgetEl.addEventListener("input", onChange);
  revEl.addEventListener("input", onChange);
  if (custEl) custEl.addEventListener("input", onChange);
  dlEl.addEventListener("input", onChange);

  // Glossary editor
  const glossaryList = document.getElementById("onb-glossary-list");
  const glossarySummary = document.getElementById("onb-glossary-summary");
  const terms = (manifest && manifest.glossary) || [];
  glossarySummary.textContent = `▾ VOCABULARY OVERRIDE (${terms.length} term${terms.length === 1 ? '' : 's'})`;
  for (const entry of terms) {
    const row = document.createElement("div");
    row.className = "onb-glossary-row";
    const overrideVal = state.data.glossary_overrides[entry.term] != null
      ? state.data.glossary_overrides[entry.term]
      : entry.definition;
    row.innerHTML = `
      <span class="onb-glossary-term">${escapeHtml(entry.term)}:</span>
      <input type="text" class="onb-glossary-input" data-term="${escapeHtml(entry.term)}" value="${escapeHtml(overrideVal)}">
    `;
    const input = row.querySelector("input");
    input.addEventListener("input", () => {
      const t = input.dataset.term;
      // Only persist as override when it differs from template default,
      // so the server can ignore unchanged rows.
      if (input.value.trim() === entry.definition.trim()) {
        delete state.data.glossary_overrides[t];
      } else {
        state.data.glossary_overrides[t] = input.value;
      }
      saveDraft();
    });
    glossaryList.appendChild(row);
  }

  refreshPreview(manifest);

  const submitBtn = document.getElementById("onb-submit-mission");
  const backBtn = document.getElementById("onb-back-mission");
  submitBtn.addEventListener("click", async () => {
    if (submitBtn.disabled) return;        // belt + braces against double-fire
    if (!state.data.initial_budget && tplBudget) state.data.initial_budget = tplBudget;
    if (!state.data.revenue_target && tplRev) state.data.revenue_target = tplRev;
    if (state.data.customer_target == null && tplCust != null) state.data.customer_target = tplCust;
    if (!state.data.deadline) state.data.deadline = defaultDeadline();

    // Did the founder actually change anything since the last
    // submission? If not, skip the LLM re-review entirely — they're
    // just back-navigating to look at the form, not revising it.
    // User feedback 2026-05-26: "只有用户改了数值才应该重新 review"
    const snap = state.data._submitted_snapshot;
    const noChange = (
      state.approval &&
      snap &&
      Number(snap.initial_budget) === Number(state.data.initial_budget) &&
      Number(snap.revenue_target) === Number(state.data.revenue_target) &&
      (snap.customer_target ?? null) === (state.data.customer_target ?? null) &&
      (snap.deadline || null) === (state.data.deadline || null)
    );
    if (noChange) {
      // Existing approval is still authoritative — jump straight to
      // review without spending more tokens.
      state.approval_stale = false;
      saveDraft();
      goto("review");
      return;
    }

    // Real change → invalidate prior review and re-run.
    state.approval = null;
    state.approval_stale = false;
    state.approval_failure_reason = null;
    state.approval_failure_id = null;
    saveDraft();
    // Lock the button + back nav so the founder can't fire duplicate
    // /onboarding/complete requests (the call takes 30-60s: template
    // apply + LLM feasibility debate). Live timer ticks every second
    // so the wait reads as "AI working" not "app stuck." Restore on
    // failure so the founder can retry; on success goto() re-renders.
    const originalLabel = submitBtn.textContent;
    submitBtn.disabled = true;
    backBtn.disabled = true;
    const submitStart = Date.now();
    const fmtSec = (s) => (s < 60 ? `${s}s` : `${Math.floor(s/60)}m ${s%60}s`);
    submitBtn.textContent = "[ ▸ team reviewing your targets… 0s ]";
    const submitTimer = setInterval(() => {
      const elapsed = Math.floor((Date.now() - submitStart) / 1000);
      submitBtn.textContent = `[ ▸ team reviewing your targets… ${fmtSec(elapsed)} ]`;
    }, 1000);
    try {
      // True change → always force a fresh review so the LLM debate
      // actually re-runs on the new numbers.
      await submitOnboarding({ forceReview: true });
    } finally {
      clearInterval(submitTimer);
      // submitOnboarding navigates to "review" on success and stays
      // on this step on failure (showError already called). Re-enable
      // only when we're still on this step — the goto/render path
      // throws this DOM away anyway.
      if (state.step === "mission") {
        submitBtn.disabled = false;
        submitBtn.textContent = originalLabel;
        backBtn.disabled = false;
      }
    }
  });
}

function defaultDeadline() {
  const d = new Date();
  d.setDate(d.getDate() + 90);
  return formatLocalISODate(d);
}

function formatLocalISODate(date) {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function refreshPreview(manifest) {
  const rowsEl = document.getElementById("onb-preview-rows");
  const dirsEl = document.getElementById("onb-preview-directives");
  const warnEl = document.getElementById("onb-preview-warning");
  if (!rowsEl) return;

  const tplBudget = manifest ? Number(manifest.initial_budget || 0) : 0;
  const tplRev = manifest ? Number(manifest.revenue_target || 0) : 0;
  const budget = Number(state.data.initial_budget != null ? state.data.initial_budget : tplBudget);
  const target = Number(state.data.revenue_target != null ? state.data.revenue_target : tplRev);

  const tplId = state.data.template_id || "blank";
  const burnCap = WEEKLY_BURN_CAP[tplId] != null ? WEEKLY_BURN_CAP[tplId] : 100;
  const roi = budget > 0 ? (target / budget) : 0;
  const runway = burnCap > 0 ? (budget / burnCap) : 0;

  rowsEl.innerHTML = `
    <div><span>starting_capital</span><b>${fmtUsd(budget)}</b></div>
    <div><span>target_capital</span><b>${fmtUsd(target)}</b></div>
    <div><span>required_roi</span><b>${roi > 0 ? roi.toFixed(1) + "×" : '--'}</b></div>
    <div><span>weekly_burn_cap</span><b>${fmtUsd(burnCap)} (${escapeHtml(tplId)})</b></div>
    <div><span>runway_at_cap</span><b>${runway > 0 ? runway.toFixed(1) + " wks" : '--'}</b></div>
    <div><span>deadline</span><b>${escapeHtml(state.data.deadline || '--')}</b></div>
  `;

  if (budget > 0 && runway < 4) {
    warnEl.hidden = false;
    warnEl.innerHTML = `⚠ Capital ${fmtUsd(budget)} below burn cap ${fmtUsd(burnCap)}/wk. Engine will throttle aggressively.`;
  } else {
    warnEl.hidden = true;
  }

  const dirs = (manifest && manifest.suggested_directives) || [];
  dirsEl.innerHTML = dirs.length
    ? dirs.map((d) => `<div class="onb-preview-dir">▸ ${escapeHtml(d)}</div>`).join("")
    : `<div class="onb-preview-dir onb-preview-dir-empty">(none — blank template)</div>`;
}

// ---------------------------------------------------------------------------
// Onboarding submit + review setup
// ---------------------------------------------------------------------------

async function submitOnboarding(opts = {}) {
  setStatus("submitting...");
  clearError();
  const body = {
    provider: state.data.provider,
    api_key: state.api_key,
    template_id: state.data.template_id,
  };
  if (state.data.base_url) body.base_url = state.data.base_url;
  if (state.data.initial_budget != null) body.initial_budget = Number(state.data.initial_budget);
  if (state.data.revenue_target != null) body.revenue_target = Number(state.data.revenue_target);
  if (state.data.customer_target != null) body.customer_target = Number(state.data.customer_target);
  if (state.data.deadline) body.deadline = state.data.deadline;
  if (Object.keys(state.data.glossary_overrides || {}).length) {
    body.glossary_overrides = state.data.glossary_overrides;
  }

  if (!state.api_key) {
    showError("API key missing — re-enter it on the Connection step.");
    return;
  }

  let res, result;
  try {
    res = await fetch("/onboarding/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    result = await res.json();
  } catch (err) {
    showError("network error: " + err.message);
    return;
  }
  if (!res.ok || result.status !== "ready") {
    showError(result.message || result.code || `HTTP ${res.status}`);
    return;
  }

  state.onboarding_result = result;
  // When the user backed out of review to revise mission and is now
  // re-submitting, /onboarding/complete takes the "reused" path which
  // currently skips auto re-running the feasibility review. Force a
  // fresh review via POST /targets/review so the new numbers actually
  // get debated.
  let reviewId = result.targets_review_id || null;
  if (opts.forceReview && !reviewId) {
    try {
      const rr = await fetch("/targets/review", { method: "POST" });
      if (rr.ok) {
        const fresh = await rr.json();
        reviewId = fresh.id || fresh.targets_review_id || null;
      }
    } catch (_) { /* fall through to no-review handling below */ }
  }
  // Fetch the approval payload + draft projects in parallel. Capture WHY
  // approval may be missing so the review screen can give an honest
  // diagnostic (no review id vs. fetch failed) instead of blaming
  // "blank template or quota error".
  const [approval, projects] = await Promise.all([
    reviewId ? fetchApproval(reviewId) : Promise.resolve(null),
    fetchDraftProjects(),
  ]);
  state.approval = approval;
  state.draft_project_ids = projects;
  if (!approval) {
    state.approval_failure_reason = reviewId ? "fetch_failed" : "no_review_id";
    state.approval_failure_id = reviewId;
  } else {
    // Snapshot the values the LLM reviewed against so a later back-to-
    // mission visit with NO edits can skip the re-review.
    state.data._submitted_snapshot = {
      initial_budget: Number(state.data.initial_budget),
      revenue_target: Number(state.data.revenue_target),
      customer_target: state.data.customer_target ?? null,
      deadline: state.data.deadline || null,
    };
    state.approval_failure_reason = null;
    state.approval_failure_id = null;
  }
  goto("review");
}

async function fetchApproval(id) {
  try {
    const res = await fetch(`/approvals/${encodeURIComponent(id)}`);
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

async function fetchDraftProjects() {
  // /projects only returns active; we hit the raw row through a small
  // back-channel: any project with status='draft' lives via direct query
  // not exposed yet. Fall back: read /templates/{id} suggested_directives
  // as labels and pair them with an /audit-derived id list later.
  // Best path right now: query /projects + filter, since draft is left
  // out of list_active. So: skip — we'll use audit-derived shape below.
  try {
    const res = await fetch("/projects?include_draft=1");
    if (!res.ok) return [];
    const rows = await res.json();
    return rows.filter((p) => p.status === "draft");
  } catch (_) {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Step 4 — Team Feasibility Review (delegates to feasibility_review.js)
// ---------------------------------------------------------------------------

function renderReview() {
  setStatus("team review");
  const host = document.createElement("div");
  host.className = "frame onb-review-frame";
  host.dataset.label = "TEAM_FEASIBILITY_REVIEW // 04.review";
  stepHost.appendChild(host);

  // Back nav: revising mission targets in light of what the team said.
  // Marks the in-memory approval as stale so renderMission shows the
  // banner; the actual re-run happens on submit (see submitOnboarding).
  const navBar = document.createElement("div");
  navBar.className = "onb-actions onb-actions-top";
  navBar.innerHTML = `
    <button type="button" class="onb-btn onb-btn-back" id="onb-back-review">[ ◂ back to mission ]</button>
  `;
  host.appendChild(navBar);
  navBar.querySelector("#onb-back-review").addEventListener("click", () => {
    // Don't flag stale on bare back-nav — staleness is now computed on
    // submit by comparing form values to the snapshot from the last
    // successful review. Going back just to LOOK at the mission step
    // shouldn't force the founder to re-burn LLM tokens.
    goto("mission");
  });

  if (!state.approval) {
    // Approval missing — give an honest cause from the captured reason
    // instead of guessing "blank template or quota error".
    let msg;
    switch (state.approval_failure_reason) {
      case "no_review_id":
        msg = "review did not run — backend returned no targets_review_id "
          + "(template may not have been applied; try re-running onboarding)";
        break;
      case "fetch_failed":
        msg = "review id received but /approvals/" + (state.approval_failure_id || "?")
          + " could not be fetched — check engine logs or retry";
        break;
      default:
        msg = "review state unavailable — check engine logs";
    }
    renderReviewFailureBanner(host, msg);
    return;
  }

  const inner = document.createElement("div");
  inner.className = "onb-review-inner";
  host.appendChild(inner);

  // No timeout watchdog here: state.approval is already a complete
  // payload (submitOnboarding awaits it before goto("review")), and
  // mountFeasibilityReview is synchronous. A timer would always fire
  // a misleading "timed out" banner on top of a fully-rendered panel
  // — that's what the user reported in 2026-05-24 testing.
  let mounted = null;

  // Fetch the founder vs team-proposal vs agreed targets trio so the
  // mount can render an explicit "TEAM PROPOSES" summary card. Without
  // this the founder has to read three columns of CEO prose to figure
  // out which numbers "adopt theirs" would actually apply.
  fetch("/targets")
    .then((r) => (r.ok ? r.json() : null))
    .then((bundle) => {
      if (bundle && mounted && typeof mounted.setTargetsBundle === "function") {
        mounted.setTargetsBundle(bundle);
      }
    })
    .catch(() => { /* non-fatal — the columns still tell the story */ });

  // Founder action wiring — adopt/keep/counter all share the same hop
  // to provisioning. Counter spawns a fresh approval thread server-side.
  mounted = mountFeasibilityReview(inner, {
    approval: state.approval,
    onAdopt: async () => {
      // Adopting the team proposal means accepting their counter-numbers.
      // The engine wires that through the approval approve path; for now
      // we just advance — the targets snapshot was already persisted.
      try {
        await fetch(`/approvals/${encodeURIComponent(state.approval.id)}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ comment: "Adopted team proposal" }),
        });
      } catch (_) { /* non-fatal */ }
      goto("first_move");
    },
    onKeep: async () => {
      try {
        await fetch(`/approvals/${encodeURIComponent(state.approval.id)}/reject`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "Founder kept original numbers" }),
        });
      } catch (_) { /* non-fatal */ }
      goto("first_move");
    },
    onCounter: async (text) => {
      // POST counter → fetch the successor approval → return for re-render.
      // Backend returns {original, successor} (see
      // engine.request_approval_revision). The successor is the new
      // pending approval the founder must act on next.
      const res = await fetch(`/approvals/${encodeURIComponent(state.approval.id)}/revise`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ counter: text }),
      });
      if (!res.ok) {
        showError("counter-propose failed: HTTP " + res.status);
        return null;
      }
      const payload = await res.json();
      const newId =
        (payload.successor && payload.successor.id) ||
        payload.new_request_id ||
        payload.id;
      if (!newId) {
        showError("counter-propose returned no successor approval id");
        return null;
      }
      const newApproval = await fetchApproval(newId);
      if (!newApproval) {
        showError("counter-propose succeeded but /approvals/" + newId + " failed to fetch");
        return null;
      }
      state.approval = newApproval;
      return newApproval;
    },
  });
}

function renderReviewFailureBanner(host, message) {
  const banner = document.createElement("div");
  banner.className = "onb-review-failure";
  banner.innerHTML = `
    <div class="onb-review-failure-msg">⚠ Team review unavailable: ${escapeHtml(message)}</div>
    <div class="onb-review-failure-actions">
      <button type="button" class="onb-btn" id="onb-review-retry">[ RETRY ]</button>
      <button type="button" class="onb-btn" id="onb-review-heuristic">[ USE HEURISTIC ]</button>
      <button type="button" class="onb-btn onb-btn-warn" id="onb-review-skip">[ SKIP REVIEW ]</button>
    </div>
  `;
  host.appendChild(banner);
  document.getElementById("onb-review-retry").addEventListener("click", async () => {
    try {
      const res = await fetch("/targets/review", { method: "POST" });
      if (res.ok) {
        state.approval = await res.json();
        state.approval_skipped = false;
        goto("review");
      } else {
        showError("retry failed: HTTP " + res.status);
      }
    } catch (err) {
      showError("retry failed: " + err.message);
    }
  });
  document.getElementById("onb-review-heuristic").addEventListener("click", () => {
    // Heuristic = quick local "feasibility" check; for now we just advance
    // with a synthetic note in the audit trail.
    state.approval_skipped = true;
    state.approval_heuristic = true;
    goto("first_move");
  });
  document.getElementById("onb-review-skip").addEventListener("click", () => {
    state.approval_skipped = true;
    goto("first_move");
  });
}

// ---------------------------------------------------------------------------
// Step 5 — First Move
// ---------------------------------------------------------------------------

async function renderFirstMove(opts) {
  setStatus("first move");
  opts = opts || {};

  // Force regen: clear stepHost immediately so the old cards visually
  // disappear, and skip the draft-resolve path so the loading-screen
  // branch always fires (which is what actually POSTs with force:true).
  if (opts.forceRegen) {
    stepHost.innerHTML = "";
    state.draft_project_ids = [];
  }

  // Step 1: resolve any existing draft projects.
  let projects = state.draft_project_ids;
  if (!projects || projects.length === 0) {
    if (!opts.forceRegen) {
      projects = await fetchDraftProjects();
      state.draft_project_ids = projects;
    } else {
      projects = [];
    }
  }

  // Step 2: if zero drafts, ask the team to PROPOSE the first three
  // directives. Contract: docs/context/operations.md:60-62 +
  // engineering-team-proposes-plan memory.
  let teamProposalPending = false;
  let teamProposalElapsed = 0;
  let proposalError = null;       // { code, message, provider } when team fails
  let proposalStatus = "ok";       // ok | team_failed | heuristic | no_targets
  if ((projects || []).length === 0) {
    const frame = document.createElement("div");
    frame.className = "frame onb-firstmove";
    frame.dataset.label = "FIRST_MOVE // 05.directive";
    frame.innerHTML = `
      <div class="onb-firstmove-head">
        <span>Team is proposing your first-week directives…</span>
        <button type="button" class="onb-link-escape" id="onb-firstmove-skip">[ ⨯ skip — show me empty dashboard ]</button>
      </div>
      <div class="fr-counter-status" id="onb-firstmove-status">
        <span class="fr-counter-spinner"></span>
        <span class="fr-counter-status-text">CEO + CRO + CPO are drafting</span>
        <span class="fr-counter-timer" id="onb-firstmove-timer">0s</span>
      </div>
      <div class="onb-actions">
        <button type="button" class="onb-btn onb-btn-back" id="onb-back-firstmove" disabled>[ ◂ back to review ]</button>
      </div>
    `;
    stepHost.appendChild(frame);
    document.getElementById("onb-firstmove-skip").addEventListener("click", () => {
      state.data.first_directive_project_id = null;
      saveDraft();
      goto("provisioning");
    });

    const startedAt = Date.now();
    const fmtSec = (s) => (s < 60 ? `${s}s` : `${Math.floor(s/60)}m ${s%60}s`);
    const timerEl = document.getElementById("onb-firstmove-timer");
    const timerHandle = setInterval(() => {
      teamProposalElapsed = Math.floor((Date.now() - startedAt) / 1000);
      if (timerEl) timerEl.textContent = fmtSec(teamProposalElapsed);
    }, 1000);

    teamProposalPending = true;
    try {
      const body = {};
      if (opts.forceHeuristic) body.force_heuristic = true;
      if (opts.forceRegen) body.force = true;
      const res = await fetch("/onboarding/propose_first_directives", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        const payload = await res.json();
        proposalStatus = (payload && payload.status) || "ok";
        if (proposalStatus === "team_failed") {
          proposalError = {
            code: payload.error_code || "unknown",
            message: payload.error_message || "(no detail)",
            provider: payload.provider || "provider",
          };
          projects = [];
        } else {
          projects = (payload && payload.directives) || [];
        }
        state.draft_project_ids = projects;
      } else {
        // Endpoint failed entirely (sidecar crash / 5xx)
        proposalStatus = "team_failed";
        proposalError = {
          code: "unknown",
          message: `HTTP ${res.status}`,
          provider: "unknown",
        };
        projects = [];
      }
    } catch (err) {
      proposalStatus = "team_failed";
      proposalError = {
        code: "network",
        message: err && err.message || String(err),
        provider: "unknown",
      };
      projects = [];
    } finally {
      clearInterval(timerHandle);
      teamProposalPending = false;
      stepHost.innerHTML = "";
    }
  }

  // Team-failed branch: render an honest error screen with retry +
  // "use starter pack" + skip. This is what Claude Code / Codex /
  // Windsurf do when LLM provider quota is gone — surface the real
  // problem, don't silently substitute a fake "AI plan."
  if (proposalError) {
    renderFirstMoveError(proposalError);
    return;
  }

  const projectCount = (projects || []).length;
  const hasStaged = projectCount > 0;

  const frame = document.createElement("div");
  frame.className = "frame onb-firstmove";
  frame.dataset.label = "FIRST_MOVE // 05.directive";

  const headerCopy = hasStaged
    ? (proposalStatus === "heuristic"
        ? `Starter pack: 3 generic first-week directives (LLM not used). Pick one to start:`
        : `Your team proposes ${projectCount} first-week directive${projectCount === 1 ? "" : "s"}. Pick one to start:`)
    : "No agreed targets yet — go back to the team review first.";

  frame.innerHTML = `
    <div class="onb-firstmove-head">
      <span>${escapeHtml(headerCopy)}</span>
      <button type="button" class="onb-link-escape" id="onb-firstmove-skip">[ ⨯ skip — show me empty dashboard ]</button>
    </div>
    ${hasStaged
      ? `<div class="onb-firstmove-list" id="onb-firstmove-list"></div>
         ${teamProposalElapsed > 0 && proposalStatus === "ok"
           ? `<p class="onb-firstmove-hint">✓ team finished in ${teamProposalElapsed < 60 ? teamProposalElapsed + "s" : Math.floor(teamProposalElapsed/60) + "m " + (teamProposalElapsed%60) + "s"}.</p>`
           : (proposalStatus === "heuristic"
              ? `<p class="onb-firstmove-hint onb-firstmove-hint-warn">⚠ heuristic seeds — your AI team didn't actually propose these. To get real team-designed directives, fix your LLM provider in settings and retry.</p>`
              : "")}
         <div class="onb-firstmove-confirm" id="onb-firstmove-confirm" hidden>
           <span class="onb-firstmove-confirm-msg">
             ⚠ Spend ~$0.30 to have the team produce 3 NEW directives?
             The current 3 will be discarded.
           </span>
           <span class="onb-firstmove-confirm-actions">
             <button type="button" class="onb-btn onb-btn-back" id="onb-firstmove-regen-cancel">[ ◂ cancel ]</button>
             <button type="button" class="onb-btn onb-btn-danger-solid" id="onb-firstmove-regen-confirm">[ ▸ CONFIRM — spend $0.30 ]</button>
           </span>
         </div>`
      : `<p class="onb-empty">Go back to the team-review step and pick keep / adopt / counter first.</p>`}
    <div class="onb-actions">
      <button type="button" class="onb-btn onb-btn-back" id="onb-back-firstmove">[ ◂ back to review ]</button>
      ${hasStaged
        ? `<button type="button" class="onb-btn onb-btn-discuss" id="onb-firstmove-regen">[ ↻ none fit — regenerate ]</button>
           <button type="button" class="onb-btn onb-btn-submit" id="onb-firstmove-start" disabled>[ ▸ START SELECTED ]</button>`
        : ""}
    </div>
  `;
  stepHost.appendChild(frame);

  document.getElementById("onb-back-firstmove").addEventListener("click", () => goto("review"));
  const regenBtn = document.getElementById("onb-firstmove-regen");
  if (regenBtn) {
    // Inline confirm panel (no native dialogs — Tauri WebView blocks
    // window.confirm). Click regen → reveal the panel under the cards
    // with explicit "spend $0.30" warning + CONFIRM / cancel buttons.
    const confirmPanel = document.getElementById("onb-firstmove-confirm");
    const confirmBtn = document.getElementById("onb-firstmove-regen-confirm");
    const cancelBtn = document.getElementById("onb-firstmove-regen-cancel");
    regenBtn.addEventListener("click", () => {
      regenBtn.disabled = true;
      if (confirmPanel) {
        confirmPanel.hidden = false;
        confirmPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });
    if (cancelBtn) {
      cancelBtn.addEventListener("click", () => {
        if (confirmPanel) confirmPanel.hidden = true;
        regenBtn.disabled = false;
      });
    }
    if (confirmBtn) {
      confirmBtn.addEventListener("click", async () => {
        confirmBtn.disabled = true;
        if (cancelBtn) cancelBtn.disabled = true;
        confirmBtn.textContent = "[ ↻ regenerating… ]";
        setStatus("team regenerating directives…");
        await renderFirstMove({ forceRegen: true });
      });
    }
  }
  document.getElementById("onb-firstmove-skip").addEventListener("click", () => {
    state.data.first_directive_project_id = null;
    saveDraft();
    goto("provisioning");
  });

  const startBtn = document.getElementById("onb-firstmove-start");

  if (hasStaged) {
    const listEl = document.getElementById("onb-firstmove-list");
    let selectedId = null;
    const renderCards = () => {
      listEl.innerHTML = "";
      for (const p of projects) {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "onb-firstmove-card";
        card.dataset.projectId = p.id;
        const proposer = (p.proposer_role || "").toUpperCase();
        const rationale = p.rationale || "";
        const weekPlan = Array.isArray(p.week_plan) ? p.week_plan : [];
        const successMetric = p.success_metric || "";
        const cost = Number(p.expected_cost_usd || 0);
        const others = (p.other_agents_involved || [])
          .map((r) => String(r).toUpperCase());
        const costLabel = cost > 0
          ? `~$${cost.toFixed(cost >= 10 ? 0 : 2)} cash spend`
          : "$0 cash spend (LLM tokens only)";
        const planBlock = weekPlan.length
          ? `<div class="onb-firstmove-week">
               <div class="onb-firstmove-section-head">// week plan</div>
               <ul class="onb-firstmove-weeklist">${
                 weekPlan.map((line) => `<li>${escapeHtml(line)}</li>`).join("")
               }</ul>
             </div>`
          : "";
        const metricBlock = successMetric
          ? `<div class="onb-firstmove-metric">
               <span class="onb-firstmove-section-head">// success metric</span>
               <span>${escapeHtml(successMetric)}</span>
             </div>`
          : "";
        const collabBlock = others.length
          ? `<div class="onb-firstmove-collab">+ ${others.map(escapeHtml).join(", ")}</div>`
          : "";
        card.innerHTML = `
          <div class="onb-firstmove-title">▸ ${escapeHtml(p.name)}</div>
          ${proposer ? `<div class="onb-firstmove-proposer">proposed by <b>${escapeHtml(proposer)}</b> ${collabBlock}</div>` : ""}
          ${rationale ? `<div class="onb-firstmove-rationale">${escapeHtml(rationale)}</div>` : ""}
          ${planBlock}
          ${metricBlock}
          <div class="onb-firstmove-cost">${escapeHtml(costLabel)}</div>
        `;
        card.addEventListener("click", () => {
          listEl.querySelectorAll(".onb-firstmove-card").forEach((c) => c.classList.remove("selected"));
          card.classList.add("selected");
          selectedId = p.id;
          startBtn.disabled = false;
        });
        listEl.appendChild(card);
      }
    };
    renderCards();

    // Founder Q&A panel — ask the team follow-up questions before
    // picking. Sits below the cards; each Q&A pair stacks. If the
    // CEO revises directives in response, cards re-render in place.
    const discussFrame = document.createElement("div");
    discussFrame.className = "onb-firstmove-discuss";
    discussFrame.innerHTML = `
      <div class="onb-firstmove-section-head">// ask the team</div>
      <div class="onb-firstmove-thread" id="onb-firstmove-thread"></div>
      <textarea class="onb-firstmove-input" id="onb-firstmove-question"
                rows="2"
                placeholder="e.g. Which is best if I have only 5 hours this week?"
                autocomplete="off" spellcheck="false"></textarea>
      <div class="onb-firstmove-discuss-actions">
        <button type="button" class="onb-btn onb-btn-discuss" id="onb-firstmove-ask" disabled>[ ▸ ASK CEO ]</button>
      </div>
    `;
    stepHost.querySelector(".onb-firstmove").appendChild(discussFrame);

    const questionEl = document.getElementById("onb-firstmove-question");
    const askBtn = document.getElementById("onb-firstmove-ask");
    const threadEl = document.getElementById("onb-firstmove-thread");
    questionEl.addEventListener("input", () => {
      askBtn.disabled = questionEl.value.trim().length === 0;
    });
    askBtn.addEventListener("click", async () => {
      const text = questionEl.value.trim();
      if (!text) return;
      const startedAt = Date.now();
      const fmtSec = (s) => (s < 60 ? `${s}s` : `${Math.floor(s/60)}m ${s%60}s`);
      askBtn.disabled = true;
      questionEl.disabled = true;
      const origLabel = askBtn.textContent;
      askBtn.textContent = "[ ▸ CEO thinking… 0s ]";
      const tHandle = setInterval(() => {
        askBtn.textContent = `[ ▸ CEO thinking… ${fmtSec(Math.floor((Date.now()-startedAt)/1000))} ]`;
      }, 1000);
      // Echo the question in the thread immediately.
      const youBlock = document.createElement("div");
      youBlock.className = "onb-firstmove-qa onb-firstmove-qa-you";
      youBlock.innerHTML = `<div class="onb-firstmove-qa-head">you</div><div>${escapeHtml(text)}</div>`;
      threadEl.appendChild(youBlock);
      let result = null;
      try {
        const res = await fetch("/onboarding/discuss_first_directives", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ question: text }),
        });
        result = res.ok ? await res.json() : { status: "team_failed", error_message: `HTTP ${res.status}` };
      } catch (err) {
        result = { status: "team_failed", error_message: err.message };
      } finally {
        clearInterval(tHandle);
        askBtn.disabled = false;
        askBtn.textContent = origLabel;
        questionEl.disabled = false;
      }
      const ceoBlock = document.createElement("div");
      ceoBlock.className = "onb-firstmove-qa onb-firstmove-qa-team";
      if (result.status === "ok" && result.answer) {
        const changed = !!result.directives_changed;
        ceoBlock.innerHTML = `
          <div class="onb-firstmove-qa-head">CEO ${changed ? '<span class="onb-firstmove-qa-revised">(revised the plan ↑)</span>' : ""}</div>
          <div>${escapeHtml(result.answer)}</div>
        `;
        if (changed && Array.isArray(result.directives) && result.directives.length) {
          projects = result.directives;
          state.draft_project_ids = projects;
          selectedId = null;
          startBtn.disabled = true;
          renderCards();
        }
      } else {
        ceoBlock.innerHTML = `
          <div class="onb-firstmove-qa-head onb-firstmove-qa-head-fail">CEO unavailable</div>
          <div>${escapeHtml(result.error_message || "(no detail)")}</div>
        `;
      }
      threadEl.appendChild(ceoBlock);
      threadEl.scrollTop = threadEl.scrollHeight;
      questionEl.value = "";
      askBtn.disabled = true;
    });
    startBtn.addEventListener("click", async () => {
      if (!selectedId) return;
      const originalLabel = startBtn.textContent;
      startBtn.disabled = true;
      startBtn.textContent = "[ ▸ activating… ]";
      try {
        const res = await fetch(`/projects/${encodeURIComponent(selectedId)}/activate`, { method: "POST" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        state.data.first_directive_project_id = selectedId;
        saveDraft();
        goto("provisioning");
      } catch (err) {
        startBtn.disabled = false;
        startBtn.textContent = originalLabel;
        showError("activate failed: " + err.message);
      }
    });
    return;
  }

}


function _firstMoveErrorCopy(code, provider) {
  // Quota / auth / network / provider_error / unknown.
  // Keep copy parallel to Claude Code / Codex / Windsurf so the
  // founder sees a familiar shape: what's wrong, why, what to do.
  switch (code) {
    case "rate_limited":
      return {
        title: `Your ${provider} quota is used up.`,
        body:
          `The team can't propose directives without an LLM call. ` +
          `Add a fallback provider in settings, top up ${provider}, ` +
          `or wait for the next billing cycle.`,
      };
    case "unauthorized":
      return {
        title: `${provider} rejected your API key.`,
        body:
          `Your key may be revoked, mistyped, or missing scope. ` +
          `Fix it in settings and retry.`,
      };
    case "network":
      return {
        title: `Couldn't reach ${provider}.`,
        body:
          `Check your network connection or your custom endpoint URL. ` +
          `Retry once the connection comes back.`,
      };
    case "provider_error":
      return {
        title: `${provider} is having problems right now.`,
        body:
          `Their service returned an unexpected error. ` +
          `Retry in a minute, or switch to a different provider in settings.`,
      };
    default:
      return {
        title: `Team couldn't propose directives.`,
        body:
          `Unknown error from ${provider}. See full message below. ` +
          `Retry, or use the built-in starter pack to continue with ` +
          `generic seeds (no AI involvement).`,
      };
  }
}


function renderFirstMoveError(errInfo) {
  // errInfo = { code, message, provider }
  const copy = _firstMoveErrorCopy(errInfo.code, errInfo.provider);
  const frame = document.createElement("div");
  frame.className = "frame onb-firstmove";
  frame.dataset.label = "FIRST_MOVE // 05.directive";
  frame.innerHTML = `
    <div class="onb-firstmove-head">
      <span>⚠ ${escapeHtml(copy.title)}</span>
      <button type="button" class="onb-link-escape" id="onb-firstmove-skip">[ ⨯ skip — show me empty dashboard ]</button>
    </div>
    <p class="onb-firstmove-error-body">${escapeHtml(copy.body)}</p>
    <details class="onb-firstmove-error-detail">
      <summary>full provider message</summary>
      <pre>${escapeHtml(errInfo.message || "(no detail)")}</pre>
    </details>
    <div class="onb-actions onb-firstmove-error-actions">
      <button type="button" class="onb-btn onb-btn-back" id="onb-back-firstmove">[ ◂ back to review ]</button>
      <button type="button" class="onb-btn onb-btn-warn" id="onb-firstmove-heuristic">[ use built-in starter pack ]</button>
      <button type="button" class="onb-btn onb-btn-submit" id="onb-firstmove-retry">[ ▸ retry team proposal ]</button>
    </div>
  `;
  stepHost.appendChild(frame);

  document.getElementById("onb-back-firstmove").addEventListener("click", () => goto("review"));
  document.getElementById("onb-firstmove-skip").addEventListener("click", () => {
    state.data.first_directive_project_id = null;
    saveDraft();
    goto("provisioning");
  });
  document.getElementById("onb-firstmove-retry").addEventListener("click", () => {
    // Re-enter step from scratch — the propose call fires again.
    state.draft_project_ids = [];
    renderFirstMove();
  });
  document.getElementById("onb-firstmove-heuristic").addEventListener("click", () => {
    // Explicit opt-in: founder accepts generic seeds. POSTs
    // force_heuristic=true so the backend persists them with the
    // distinct ``team_proposal_first_week_heuristic`` source marker.
    state.draft_project_ids = [];
    renderFirstMove({ forceHeuristic: true });
  });
}

// ---------------------------------------------------------------------------
// Step 6 — Provisioning
// ---------------------------------------------------------------------------

async function renderProvisioning() {
  setStatus("provisioning");
  const frame = document.createElement("div");
  frame.className = "frame onb-provision";
  frame.dataset.label = "PROVISIONING // 06.commit";
  frame.innerHTML = `
    <div class="onb-provision-lines" id="onb-provision-lines"></div>
    <div class="onb-provision-strip" id="onb-provision-strip"></div>
  `;
  stepHost.appendChild(frame);
  const linesEl = document.getElementById("onb-provision-lines");
  const stripEl = document.getElementById("onb-provision-strip");

  const result = state.onboarding_result || {};
  const provider = state.data.provider;
  const tplId = state.data.template_id;
  const budget = state.data.initial_budget;
  const rev = state.data.revenue_target;
  const dl = state.data.deadline;
  const glossaryCount = Object.keys(state.data.glossary_overrides || {}).length;
  const dirText = state.data.first_directive_project_id
    ? `selected project ${state.data.first_directive_project_id.slice(0, 12)}`
    : "skipped — empty dashboard";

  const lines = [
    { tag: "OK", text: `Vault: stored ${provider} API key, encrypted with local key` },
    { tag: "OK", text: `Ledger: initialized at ${fmtUsd(budget || 0)} USD` },
    { tag: "OK", text: `Goal memory: targets registered (rev ${fmtUsd(rev || 0)} by ${dl || '--'})` },
    { tag: "OK", text: `Faction policy: ${tplId} loaded` },
    { tag: "OK", text: `Roster: agents spawned` },
    { tag: "OK", text: `Glossary: ${glossaryCount} term override${glossaryCount === 1 ? '' : 's'} applied` },
    { tag: "..", text: `First directive: ${dirText}` },
  ];

  const runId = result.run_id || ("run_" + Math.random().toString(16).slice(2, 10));
  const t0 = Date.now();
  function fmtClock() {
    const sec = ((Date.now() - t0) / 1000).toFixed(1);
    return `T+ 00:00:${sec.padStart(4, "0")}`;
  }

  for (const ln of lines) {
    linesEl.innerHTML += `<div class="onb-provision-line">[${ln.tag}] ${escapeHtml(ln.text)}</div>`;
    stripEl.textContent = `STATUS: PROVISIONING  ·  CONN: 127.0.0.1:8765  ·  RUN_ID: ${runId}  ·  ${fmtClock()}`;
    await sleep(180);
  }

  // Wipe draft now that the install is committed.
  dropDraft();
  setStatus("ready — loading dashboard");

  // Brief pause so the founder sees the final state before pivot.
  await sleep(800);
  window.location.replace("/ui/");
}

// ---------------------------------------------------------------------------
// Cyberpunk combobox (copied from prior file)
// ---------------------------------------------------------------------------

function initCombobox(rootId) {
  const root = document.getElementById(rootId);
  if (!root) return;
  const targetId = root.dataset.target;
  const hidden = document.getElementById(targetId);
  const button = root.querySelector(".cyb-combobox-button");
  const list = root.querySelector(".cyb-combobox-list");
  const options = Array.from(list.querySelectorAll(".cyb-combobox-option"));

  function setOpen(open) {
    root.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      const sel = options.find((o) => o.dataset.value === hidden.value) || options[0];
      options.forEach((o) => o.removeAttribute("data-active"));
      sel.setAttribute("data-active", "true");
      sel.scrollIntoView({ block: "nearest" });
      document.addEventListener("click", onDocClick, true);
      document.addEventListener("keydown", onKeyDown, true);
    } else {
      document.removeEventListener("click", onDocClick, true);
      document.removeEventListener("keydown", onKeyDown, true);
    }
  }

  function selectOption(opt) {
    if (!opt) return;
    options.forEach((o) => o.removeAttribute("aria-selected"));
    opt.setAttribute("aria-selected", "true");
    hidden.value = opt.dataset.value;
    button.textContent = opt.textContent;
    hidden.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function onDocClick(evt) {
    if (!root.contains(evt.target)) setOpen(false);
  }

  function onKeyDown(evt) {
    const isOpen = root.getAttribute("aria-expanded") === "true";
    if (!isOpen) return;
    const active = list.querySelector('[data-active="true"]') || options[0];
    let idx = options.indexOf(active);
    if (evt.key === "Escape") {
      setOpen(false);
      button.focus();
      evt.preventDefault();
    } else if (evt.key === "ArrowDown") {
      idx = (idx + 1) % options.length;
      active.removeAttribute("data-active");
      options[idx].setAttribute("data-active", "true");
      options[idx].scrollIntoView({ block: "nearest" });
      evt.preventDefault();
    } else if (evt.key === "ArrowUp") {
      idx = (idx - 1 + options.length) % options.length;
      active.removeAttribute("data-active");
      options[idx].setAttribute("data-active", "true");
      options[idx].scrollIntoView({ block: "nearest" });
      evt.preventDefault();
    } else if (evt.key === "Enter" || evt.key === " ") {
      selectOption(options[idx]);
      setOpen(false);
      button.focus();
      evt.preventDefault();
    }
  }

  button.addEventListener("click", (evt) => {
    evt.preventDefault();
    setOpen(root.getAttribute("aria-expanded") !== "true");
  });
  options.forEach((opt) => {
    opt.addEventListener("click", (evt) => {
      evt.preventDefault();
      selectOption(opt);
      setOpen(false);
      button.focus();
    });
  });
}

// ---------------------------------------------------------------------------
// Boot the wizard (resume gate first)
// ---------------------------------------------------------------------------

async function start() {
  const draft = loadDraft();
  const firstBootDone = (() => {
    try { return !!localStorage.getItem(FIRST_BOOT_KEY); } catch (_) { return false; }
  })();

  // SERVER-SIDE resume signal beats the localStorage draft. If the
  // founder already paid for the team feasibility debate (LLM tokens
  // burned) and only abandoned the review step, jump straight back to
  // review with the existing approval — don't make them restart and
  // re-burn tokens. The /onboarding/status endpoint returns
  // pending_target_feasibility_approval_id when the trail is hot.
  try {
    const statusRes = await fetch("/onboarding/status");
    if (statusRes.ok) {
      const status = await statusRes.json();
      if (status.pending_target_feasibility_approval_id && !status.agreed_targets_set) {
        const approval = await fetchApproval(status.pending_target_feasibility_approval_id);
        if (approval) {
          state.approval = approval;
          state.approval_failure_reason = null;
          state.approval_failure_id = null;
          // Hydrate the saved form data if any — so back-nav from
          // review re-fills mission inputs correctly.
          if (draft && draft.data) Object.assign(state.data, draft.data);
          setStatus("welcome back — team finished reviewing your targets");
          goto("review");
          return;
        }
      }
      // Resume-to-step-5: agreed targets set, drafts exist, no active
      // project. Founder quit mid first-move. Drop them back on step 5
      // with the existing drafts pre-loaded so the team's directive
      // proposal isn't buried in the dashboard inbox.
      if (status.pending_first_move) {
        if (draft && draft.data) Object.assign(state.data, draft.data);
        // Pre-populate the draft list so renderFirstMove() short-
        // circuits the propose call — the team already proposed; no
        // extra tokens spent on the resume.
        state.draft_project_ids = await fetchDraftProjects();
        setStatus("welcome back — pick a first-week directive");
        goto("first_move");
        return;
      }
    }
  } catch (_) {
    // Status probe is best-effort. Fall through to the local-draft path.
  }

  if (draft && draft.step && STEP_ORDER.includes(draft.step)) {
    const expired = draftIsExpired(draft);
    showResumeBanner(draft, expired);
    return;
  }

  // No draft. Skip boot sequence on subsequent installs.
  if (firstBootDone) {
    goto("connection");
  } else {
    goto("boot");
  }
}

function showResumeBanner(draft, expired) {
  resumeBanner.hidden = false;
  resumeMetaEl.textContent = `saved ${formatAgo(draft.saved_at)} · step: ${STEP_LABELS[draft.step] || draft.step}`;
  if (expired) {
    resumeBanner.classList.add("onb-resume-expired");
    resumeBanner.querySelector(".onb-resume-head").textContent = "▾ EXPIRED DRAFT (7d+ old)";
    // Swap default: discard is the recommended action for stale drafts.
    resumeYesBtn.textContent = "[ resume anyway ]";
    resumeNoBtn.textContent = "[ discard old draft ]";
  }

  resumeYesBtn.addEventListener("click", () => {
    resumeBanner.hidden = true;
    // Restore everything except api_key.
    Object.assign(state.data, draft.data || {});
    // Make sure the post-connection steps can re-enter cleanly.
    if (draft.step === "boot") {
      goto("connection");
    } else if (draft.step === "review" || draft.step === "first_move" || draft.step === "provisioning") {
      // These steps require server-side state we no longer have a
      // reliable handle on (approval id wasn't persisted). Drop back to
      // mission to re-submit with the same numbers.
      goto("mission");
    } else {
      goto(draft.step);
    }
  });
  resumeNoBtn.addEventListener("click", () => {
    dropDraft();
    resumeBanner.hidden = true;
    try {
      const seenBoot = localStorage.getItem(FIRST_BOOT_KEY);
      if (seenBoot) goto("connection");
      else goto("boot");
    } catch (_) {
      goto("connection");
    }
  });
}

start();
