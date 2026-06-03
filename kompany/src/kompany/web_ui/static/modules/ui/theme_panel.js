/* Theme switcher panel — feature A, plan item #4.
 *
 * A header trigger (◑) opens a popover with: theme swatches (pick by mood),
 * the AUTO ambient toggle (feature #5 reads `data-ambient`; rendering lands
 * with #5), and a tri-state MOTION control (Auto/On/Reduced, decision #10).
 *
 * Swatch previews render each palette WITHOUT applying it globally: the swatch
 * element carries its own `data-theme`, and the preview paints with
 * `rgb(var(--*-rgb))` so the triples resolve locally to that theme's colours.
 */

import {
  THEMES,
  getTheme,
  setTheme,
  getAuto,
  setAuto,
  getMotionMode,
  setMotionMode,
} from "/ui/static/modules/theme.js";

let panelEl = null;
let triggerEl = null;

export function initThemePanel() {
  // Mount the trigger into the right-pinned actions group (post header
  // restructure) so it sits next to settings, not after the hideable stats.
  // Other/older headers without .header-actions fall back to .stats.
  const mount =
    document.querySelector(".header .header-actions") ||
    document.querySelector(".header .stats");
  if (!mount || document.querySelector(".theme-trigger")) return;

  triggerEl = document.createElement("button");
  triggerEl.className = "theme-trigger";
  triggerEl.type = "button";
  triggerEl.title = "Theme & display";
  triggerEl.setAttribute("aria-label", "Theme & display");
  triggerEl.setAttribute("aria-expanded", "false");
  triggerEl.textContent = "◑";
  triggerEl.addEventListener("click", (e) => {
    e.stopPropagation();
    togglePanel();
  });
  // Place AFTER the settings link: settings then ◑ (matches the screenshot).
  mount.appendChild(triggerEl);
}

function togglePanel() {
  if (panelEl) return closePanel();
  openPanel();
}

function openPanel() {
  panelEl = document.createElement("div");
  panelEl.className = "theme-panel";
  panelEl.addEventListener("click", (e) => e.stopPropagation());
  render();
  document.body.appendChild(panelEl);
  triggerEl.setAttribute("aria-expanded", "true");
  document.addEventListener("click", closePanel);
  document.addEventListener("keydown", onKey);
}

function closePanel() {
  if (!panelEl) return;
  panelEl.remove();
  panelEl = null;
  triggerEl.setAttribute("aria-expanded", "false");
  document.removeEventListener("click", closePanel);
  document.removeEventListener("keydown", onKey);
}

function onKey(e) {
  if (e.key === "Escape") closePanel();
}

function render() {
  const activeTheme = getTheme();
  const motion = getMotionMode();
  const auto = getAuto();

  const swatches = THEMES.map((t) => {
    const on = t.id === activeTheme ? " active" : "";
    // data-theme on the swatch makes the preview resolve THIS theme's triples.
    return `
      <button class="tp-swatch${on}" type="button" data-pick="${t.id}" data-theme="${t.id}">
        <span class="tp-preview">
          <span class="tp-pv-bar" style="background: rgb(var(--bg-rgb));">
            <span class="tp-pv-fg" style="color: rgb(var(--fg-rgb));">Aa</span>
            <span class="tp-pv-dot" style="background: rgb(var(--warn-rgb));"></span>
            <span class="tp-pv-dot" style="background: rgb(var(--alert-rgb));"></span>
            <span class="tp-pv-dot" style="background: rgb(var(--info-rgb));"></span>
          </span>
        </span>
        <span class="tp-meta">
          <span class="tp-label">${t.label}${on ? " ✓" : ""}</span>
          <span class="tp-mood">${t.mood}</span>
        </span>
      </button>`;
  }).join("");

  const motBtn = (mode, label) =>
    `<button class="tp-seg${motion === mode ? " active" : ""}" type="button" data-motion="${mode}">${label}</button>`;

  panelEl.innerHTML = `
    <div class="tp-section-head">THEME</div>
    <div class="tp-swatches">${swatches}</div>

    <div class="tp-section-head">AUTO AMBIENT</div>
    <label class="tp-row">
      <input type="checkbox" id="tp-auto" ${auto ? "checked" : ""}>
      <span class="tp-row-text">tint by business state<span class="tp-row-sub">calm · alert · thriving (overlay only)</span></span>
    </label>

    <div class="tp-section-head">MOTION</div>
    <div class="tp-seg-group">
      ${motBtn("auto", "Auto")}${motBtn("on", "On")}${motBtn("off", "Reduced")}
    </div>
    <div class="tp-hint">Auto follows your system reduced-motion setting.</div>
  `;

  panelEl.querySelectorAll("[data-pick]").forEach((b) =>
    b.addEventListener("click", () => {
      setTheme(b.getAttribute("data-pick"));
      render();
    })
  );
  panelEl.querySelector("#tp-auto").addEventListener("change", (e) => {
    setAuto(e.target.checked);
  });
  panelEl.querySelectorAll("[data-motion]").forEach((b) =>
    b.addEventListener("click", () => {
      setMotionMode(b.getAttribute("data-motion"));
      render();
    })
  );
}
