// Kompany web UI entry point. Orchestrates store + REST initial load +
// SSE live updates. Vanilla ES modules — no build step.

import { store } from "/ui/static/modules/store.js";
import { api } from "/ui/static/modules/api.js";
import { connectSSE } from "/ui/static/modules/sse.js";
import { renderOffice } from "/ui/static/modules/ui/office.js";
import { renderInbox } from "/ui/static/modules/ui/inbox.js";
import { initTimeline, pushTimeline } from "/ui/static/modules/ui/timeline.js";
import { renderLedger } from "/ui/static/modules/ui/ledger.js";
import { renderEpisodes } from "/ui/static/modules/ui/episodes.js";
import { initDirective } from "/ui/static/modules/ui/directive.js";
import { initCostChip, getCostChip } from "/ui/static/modules/ui/cost_chip.js";

async function boot() {
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
  initCostChip();
  initTimeline();
  initDirective(async (text) => {
    pushTimeline({ level: "warn", text: `directive submitted: ${text}` });
    try {
      const result = await api.directive(text);
      pushTimeline({
        level: result.status === "ok" || result.status === "completed" ? "ok" : "warn",
        text: `directive ${result.status}: ${result.message || ""}`,
      });
      // Refresh inbox after directive (an approval may have appeared).
      api.inbox().then((rows) => store.update("inbox", rows)).catch(() => {});
    } catch (err) {
      pushTimeline({ level: "err", text: `directive failed: ${err.message}` });
    }
  });

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
