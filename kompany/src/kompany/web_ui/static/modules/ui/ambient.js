/* Ambient layer — feature A, plan item #5.
 *
 * A thin business-state overlay that tints the dashboard edges WITHOUT touching
 * the chosen base theme (decision #4). Three states (decision: calm/alert/thriving),
 * computed purely client-side from store `status` + `inbox` — zero backend.
 *
 * Active only when the founder turned AUTO on in the theme panel
 * (`data-ambient="on"`). The overlay reads `data-ambient-state` on <html>; the
 * pulse animation is killed automatically under reduce-motion, leaving the COLOUR
 * cue intact (decision #10 — alerts never depend on flash).
 */

import { store } from "/ui/static/modules/store.js?v=2";
import { getAuto } from "/ui/static/modules/theme.js";

// Runway fraction at/below which we treat the company as in alert.
const RUNWAY_ALERT_FRACTION = 0.2;

export function initAmbient() {
  if (!document.querySelector(".ambient-overlay")) {
    const el = document.createElement("div");
    el.className = "ambient-overlay";
    el.setAttribute("aria-hidden", "true");
    document.body.appendChild(el);
  }
  store.subscribe("status", evaluate);
  store.subscribe("inbox", evaluate);
  // Panel toggled AUTO — re-evaluate immediately (see theme.js setAuto).
  window.addEventListener("kompany:auto", evaluate);
  evaluate();
}

function evaluate() {
  const html = document.documentElement;
  if (!getAuto()) {
    // AUTO off → no ambient. data-ambient is already "off"; clear state too.
    html.removeAttribute("data-ambient-state");
    return;
  }
  html.setAttribute("data-ambient-state", computeState(store.state.status, store.state.inbox));
}

function computeState(status, inbox) {
  status = status || {};
  const highSeverity = (inbox || []).some(
    (e) => e && (e.severity === "high" || e.severity === "critical"),
  );

  const budget = Number(status.virtual_days_budget || 0);
  const remaining = Number(status.virtual_days_remaining || 0);
  const lowRunway = budget > 0 && remaining / budget <= RUNWAY_ALERT_FRACTION;

  // Only treat zero/negative cash as an alert once balance is actually known,
  // so a cold boot with no /status yet doesn't flash red.
  const broke = status.balance != null && Number(status.balance) <= 0;

  if (highSeverity || lowRunway || broke) return "alert";

  const income = Number(status.total_income || 0);
  if (income > 0) return "thriving";

  return "calm";
}
