// Kompany web UI entry point. Orchestrates store + REST initial load +
// SSE live updates. Vanilla ES modules — no build step.

import { store } from "/ui/static/modules/store.js";
import { api } from "/ui/static/modules/api.js";
import { connectSSE } from "/ui/static/modules/sse.js";
import { renderOffice } from "/ui/static/modules/ui/office.js";
import { renderInbox } from "/ui/static/modules/ui/inbox.js";
import { initTimeline, pushTimeline } from "/ui/static/modules/ui/timeline.js";
import { initTimelineModal } from "/ui/static/modules/ui/timeline_modal.js";
import { renderLedger } from "/ui/static/modules/ui/ledger.js?v=2";
import { renderEpisodes, showAgentTasks } from "/ui/static/modules/ui/episodes.js?v=5";
import { initChannel, channelHandleEvent } from "/ui/static/modules/ui/channel.js";
import { initCostChip, getCostChip } from "/ui/static/modules/ui/cost_chip.js";
import { initTheme } from "/ui/static/modules/theme.js";
import { initThemePanel } from "/ui/static/modules/ui/theme_panel.js?v=2";
import { initAmbient } from "/ui/static/modules/ui/ambient.js";

async function boot() {
  // Re-assert theme/motion (the inline <head> script set them for first paint)
  // and bind the OS reduced-motion listener for un-pinned founders.
  initTheme();

  // 0. Onboarding gate — Tauri shell points the WebView at /ui/ on every
  // launch, so we must redirect first-time users into the in-window
  // wizard before any /inbox or /agents/status request races against
  // an uninitialised engine. The fetch is fail-open: if the endpoint
  // 5xx's we still load the dashboard, on the theory that an old
  // sidecar with no /onboarding route is still a valid install.
  try {
    const onb = await fetch("/onboarding/status", {
      headers: { Accept: "application/json" },
    });
    if (onb.ok) {
      const snap = await onb.json();
      if (snap && snap.onboarded === false) {
        window.location.replace("/ui/onboarding.html");
        return;
      }
      // Resume-from-review: template applied but the founder never
      // acted on the team feasibility approval. Drop them back on the
      // wizard's review step so the LLM debate they already paid for
      // isn't buried in the inbox.
      if (
        snap &&
        snap.onboarded === true &&
        snap.pending_target_feasibility_approval_id &&
        snap.agreed_targets_set === false
      ) {
        window.location.replace("/ui/onboarding.html");
        return;
      }
      // Resume-from-first-move: agreed targets set but founder never
      // activated a directive (drafts exist, no active project). Drop
      // them back at step 5 so the team's directive proposal isn't
      // scattered as raw inbox items.
      if (
        snap &&
        snap.onboarded === true &&
        snap.pending_first_move === true
      ) {
        window.location.replace("/ui/onboarding.html");
        return;
      }
    }
  } catch (_err) {
    // Network blip — fall through to normal boot.
  }

  // 1. Fetch initial snapshot in parallel.
  const [inboxData, episodesData, agentsData, statusData] =
    await Promise.allSettled([
      api.inbox(),
      api.episodes(),
      api.agentsStatus(),
      api.status(),
    ]);

  if (inboxData.status === "fulfilled") {
    store.update("inbox", inboxData.value);
  }
  if (episodesData.status === "fulfilled") {
    store.update("episodes", episodesData.value);
  }
  if (agentsData.status === "fulfilled") {
    store.update("agents", agentsData.value);
  }
  if (statusData.status === "fulfilled") {
    store.update("status", statusData.value);
  }

  // 2. Wire UI modules to store updates.
  store.subscribe("agents", renderOffice);
  store.subscribe("inbox", renderInbox);
  store.subscribe("episodes", renderEpisodes);
  store.subscribe("status", renderLedger);
  store.subscribe("sse", onSseStatus);

  // 3. First render with whatever we already have.
  renderOffice(store.state.agents || []);
  renderInbox(store.state.inbox || []);
  renderEpisodes(store.state.episodes || []);
  renderLedger(store.state.status || {});

  // Defensive 10s poll: SSE pushes status updates but the boot fetch
  // sometimes races (engine still warming up, sidecar not ready to
  // serve /status). If that boot fetch failed, the header stuck on
  // cash $0 even though the ledger had $50. Poll keeps the header
  // honest while the team auto-kickoff runs in the background and
  // burns LLM tokens.
  setInterval(async () => {
    try {
      const fresh = await api.status();
      store.update("status", fresh);
    } catch (_) { /* swallow — next tick retries */ }
  }, 10000);
  // Also re-fetch agents + inbox + episodes on the same cadence so a
  // background project execution surfaces in the UI without SSE.
  setInterval(async () => {
    try {
      const [a, i, e] = await Promise.allSettled([
        api.agentsStatus(), api.inbox(), api.episodes(),
      ]);
      if (a.status === "fulfilled") store.update("agents", a.value);
      if (i.status === "fulfilled") store.update("inbox", i.value);
      if (e.status === "fulfilled") store.update("episodes", e.value);
    } catch (_) {}
  }, 7000);

  // Staff-panel click → show that agent's tasks in the episode panel.
  document.addEventListener("agent-click", (e) => {
    const role = e.detail && e.detail.role;
    if (role) showAgentTasks(role);
  });

  initCostChip();
  initThemePanel();
  initAmbient();
  initTimeline();
  // Timeline is hidden by default; this wires the LOG affordance, modal
  // overlay, and restores the persisted pin/dock state.
  initTimelineModal();
  // Backfill the timeline with recent audit history so a run that
  // happened before this EventSource connected (e.g. the kickoff that
  // fired during onboarding) is visible instead of an empty "ready."
  fetch("/audit/recent?limit=40", { headers: { Accept: "application/json" } })
    .then((r) => (r.ok ? r.json() : []))
    .then((events) => {
      for (const ev of events || []) {
        const type = ev.event_type || "";
        let level = "ok";
        if (type.includes("fail") || type.includes("err")) level = "err";
        else if (type.startsWith("health") || type.includes("strand")) level = "warn";
        const who = ev.agent_role ? ` [${ev.agent_role}]` : "";
        pushTimeline({ level, text: `${type}${ev.action ? " " + ev.action : ""}${who}` });
      }
    })
    .catch(() => { /* non-fatal */ });
  // CEO channel owns the directive bar now (06-03-ceo-channel, PR4). It
  // renders the founder↔team thread above the input, drives send/clarify/
  // gate/final, and reuses the single SSE feed via channelHandleEvent.
  // directive.js is left untouched for any non-dashboard caller but is no
  // longer wired here (no dead dual handlers on the input).
  initChannel();

  // 4. Start the live SSE feed.
  connectSSE("/events", {
    onOpen: () => store.update("sse", { online: true }),
    onError: () => store.update("sse", { online: false }),
    onEvent: handleEvent,
  });
}

function onSseStatus({ online }) {
  const el = document.getElementById("stat-sse");
  if (!el) return;
  el.textContent = online ? "online" : "offline";
  el.classList.remove("online", "offline", "connecting");
  el.classList.add(online ? "online" : "offline");
}

function handleEvent(evt) {
  // evt: { type, data, id }
  const { type, data } = evt;
  if (!type) return;

  // Push every event into the live timeline. Color by category.
  let level = "ok";
  if (type.startsWith("health.")) level = "warn";
  if (type.includes("err") || type.includes("failed") || type.includes("retry_exhausted")) level = "err";
  pushTimeline({
    level,
    text: `${type}${data.action ? " " + data.action : ""}${data.agent_role ? " [" + data.agent_role + "]" : ""}`,
  });

  // LLM spend chip — incremental update.
  if (type === "llm.spend") {
    const chip = getCostChip();
    if (chip) chip.onSpend(data || {});
  }

  // CEO channel bubble: live cost (llm.spend), status lines (audit.*),
  // and session reconcile (channel.updated). Reuses the single SSE feed.
  channelHandleEvent(type, data || {});

  // Dispatch type-specific store refreshes.
  if (type === "inbox.updated") {
    api.inbox().then((rows) => store.update("inbox", rows)).catch(() => {});
  } else if (type === "episode.recorded") {
    api.episodes().then((rows) => store.update("episodes", rows)).catch(() => {});
    api.status().then((s) => store.update("status", s)).catch(() => {});
  } else if (type.startsWith("audit.")) {
    // Audit may move an agent in/out of "busy" — refresh on a debounced
    // schedule so we don't hammer the server.
    scheduleAgentRefresh();
    if (data.event_type === "ledger.recorded" || (data.action || "").includes("ledger")) {
      api.status().then((s) => store.update("status", s)).catch(() => {});
    }
  } else if (type === "health.event") {
    // No store data to refresh — the timeline line already covers it.
  }
}

let _agentTimer = null;
function scheduleAgentRefresh() {
  if (_agentTimer) return;
  _agentTimer = setTimeout(() => {
    _agentTimer = null;
    api.agentsStatus().then((rows) => store.update("agents", rows)).catch(() => {});
  }, 500);
}

boot().catch((err) => {
  console.error("boot failed", err);
  const tl = document.getElementById("timeline");
  if (tl) tl.innerHTML = `<div class="line err">boot failed: ${err.message}</div>`;
});
