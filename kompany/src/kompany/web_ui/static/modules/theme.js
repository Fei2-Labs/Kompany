/* Theme runtime — feature A.
 *
 * Applies a `data-theme` + `data-motion` to <html> and persists the choice to
 * localStorage. The first-paint inline script in the page <head> already sets
 * the attributes before CSS loads (no flash); this module is the source of truth
 * for RUNTIME switching (the theme panel imports `setTheme`/`setMotion`).
 *
 * Persistence: localStorage now (decision #7 — fast paint); backend DB mirror is
 * a separate plan item (#8, GET/PATCH /preferences) wired later. `pushRemote` is
 * the seam for that — currently a no-op.
 */

export const THEME_KEY = "kompany.theme";
export const MOTION_KEY = "kompany.motion";
export const AUTO_KEY = "kompany.auto"; // ambient-layer auto mode (feature #5 reads this)

// id MUST match a [data-theme="…"] block in themes.css. `mood` is the founder-
// facing label for the future picker.
export const THEMES = [
  { id: "cyberpunk", label: "CYBERPUNK",  mood: "cold focus · neon terminal" },
  { id: "warm",      label: "WARM",       mood: "warm focus · soft amber" },
  { id: "minimal",   label: "MINIMAL",    mood: "bright · clean light" },
  { id: "midnight",  label: "MIDNIGHT",   mood: "calm · deep navy" },
];

const VALID = new Set(THEMES.map((t) => t.id));
export const DEFAULT_THEME = "cyberpunk";

// `?theme=`/`?motion=` URL overrides — deep-linkable theme + a test seam. URL
// wins over localStorage; the inline <head> bootstrap honours the same params
// so there's no first-paint flash when deep-linked.
function urlParam(name) {
  try {
    return new URLSearchParams(window.location.search).get(name);
  } catch (e) {
    return null;
  }
}

export function getTheme() {
  const u = urlParam("theme");
  const t = VALID.has(u) ? u : localStorage.getItem(THEME_KEY);
  return VALID.has(t) ? t : DEFAULT_THEME;
}

export function getMotion() {
  const m = getMotionMode();
  if (m === "on" || m === "off") return m;
  // No stored choice → follow the OS (decision #10).
  return prefersReducedMotion() ? "off" : "on";
}

// Tri-state for the panel: "auto" (follow OS) | "on" | "off".
export function getMotionMode() {
  const u = urlParam("motion");
  const m = u === "on" || u === "off" || u === "auto" ? u : localStorage.getItem(MOTION_KEY);
  return m === "on" || m === "off" ? m : "auto";
}

// Ambient auto mode (feature #5). Stored as "1"/"0"; default off.
export function getAuto() {
  return localStorage.getItem(AUTO_KEY) === "1";
}

export function applyAuto(on) {
  document.documentElement.setAttribute("data-ambient", on ? "on" : "off");
  return !!on;
}

export function setAuto(on) {
  const v = applyAuto(on);
  localStorage.setItem(AUTO_KEY, v ? "1" : "0");
  pushRemote({ auto_enabled: v });
  // Let the ambient layer re-evaluate immediately (it reads getAuto()).
  window.dispatchEvent(new CustomEvent("kompany:auto"));
  return v;
}

export function prefersReducedMotion() {
  return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

export function applyTheme(id) {
  const theme = VALID.has(id) ? id : DEFAULT_THEME;
  document.documentElement.setAttribute("data-theme", theme);
  return theme;
}

export function applyMotion(value) {
  const m = value === "off" ? "off" : "on";
  document.documentElement.setAttribute("data-motion", m);
  return m;
}

export function setTheme(id) {
  const theme = applyTheme(id);
  localStorage.setItem(THEME_KEY, theme);
  pushRemote({ theme_id: theme });
  return theme;
}

export function setMotion(value) {
  const m = applyMotion(value);
  localStorage.setItem(MOTION_KEY, m);
  pushRemote({ reduce_motion: m }); // "on" | "off"
  return m;
}

// Panel control: "auto" clears the pin and follows the OS; "on"/"off" pin it.
export function setMotionMode(mode) {
  if (mode === "on" || mode === "off") return setMotion(mode);
  localStorage.removeItem(MOTION_KEY);
  applyMotion(prefersReducedMotion() ? "off" : "on");
  pushRemote({ reduce_motion: "auto" });
  return "auto";
}

/* Reconcile attributes with stored/derived state on boot. Safe to call after the
 * inline script — it just re-asserts the same values and binds the OS-pref
 * listener so an un-pinned founder follows system changes live. */
export function initTheme() {
  applyTheme(getTheme());
  applyMotion(getMotion());
  applyAuto(getAuto());
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => {
      if (localStorage.getItem(MOTION_KEY) == null) applyMotion(mq.matches ? "off" : "on");
    };
    mq.addEventListener ? mq.addEventListener("change", onChange) : mq.addListener(onChange);
  }
  // DB is the source of truth (decision #7); reconcile after first paint. A URL
  // override (?theme=) wins for this view and is NOT clobbered by the remote.
  syncRemote();
}

/* Pull DB preferences and apply them, refreshing the localStorage fast-paint
 * cache. Fire-and-forget: on any failure we keep the local values. */
async function syncRemote() {
  let prefs;
  try {
    const res = await fetch("/preferences", { headers: { Accept: "application/json" } });
    if (!res.ok) return;
    prefs = await res.json();
  } catch (e) {
    return;
  }
  if (!prefs) return;

  if (!urlParam("theme") && prefs.theme_id && prefs.theme_id !== getTheme()) {
    localStorage.setItem(THEME_KEY, prefs.theme_id);
    applyTheme(prefs.theme_id);
  }

  if (typeof prefs.auto_enabled === "boolean" && prefs.auto_enabled !== getAuto()) {
    localStorage.setItem(AUTO_KEY, prefs.auto_enabled ? "1" : "0");
    applyAuto(prefs.auto_enabled);
    window.dispatchEvent(new CustomEvent("kompany:auto"));
  }

  if (!urlParam("motion") && prefs.reduce_motion && prefs.reduce_motion !== getMotionMode()) {
    if (prefs.reduce_motion === "auto") {
      localStorage.removeItem(MOTION_KEY);
      applyMotion(prefersReducedMotion() ? "off" : "on");
    } else {
      localStorage.setItem(MOTION_KEY, prefs.reduce_motion);
      applyMotion(prefs.reduce_motion);
    }
  }
}

/* Mirror a local change to the DB (decision #7: DB truth, localStorage cache).
 * Fire-and-forget — the WebView keeps working offline; the next change retries. */
function pushRemote(partial) {
  try {
    fetch("/preferences", {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(partial || {}),
    }).catch(() => {});
  } catch (e) {
    /* ignore — localStorage already holds the value for this session */
  }
}
