// Nav model for the Linear-style left rail. Real content for each pane
// lands in PR2-PR4; PR1 routes every target to an empty placeholder.

export interface NavItem {
  /** Route path, or null for an external link / non-route action. */
  path: string | null;
  label: string;
  /** Small leading glyph (text, not an icon font — keeps the bundle lean). */
  glyph?: string;
  /** External href (opens in the same WebView; e.g. the cyberpunk terminal). */
  href?: string;
  /** Rendered but not yet wired — shows a muted "soon" tag. */
  disabled?: boolean;
  /** Override the muted disabled tag (defaults to "soon"). */
  disabledTag?: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

// Top, ungrouped items (above the first group).
export const TOP_ITEMS: NavItem[] = [
  { path: '/talk', label: 'kompany>', glyph: '›' },
  { path: '/needs-you', label: 'Needs You', glyph: '!' },
];

// Secondary "Live" views. The cyberpunk terminal is served by FastAPI at
// `/ui/` (verified: api.py StaticFiles mount). The Phaser world lives in the
// standalone `kompany-world` repo and is NOT mounted by the engine yet — we
// link it as a clearly-marked, disabled TODO stub rather than fabricate a
// route. Wire its href once kompany-world's dist/ is mounted (e.g. `/world/`).
export const LIVE_ITEMS: NavItem[] = [
  // Internal route, NOT a raw `href`: `<a href="/ui/">` navigates the whole
  // Tauri WebView away from the SPA, and the old terminal has no link back — the
  // founder gets stranded. `/live` embeds `/ui/` in an iframe inside the shell,
  // so the nav rail stays and clicking Board (or anything) returns instantly.
  { path: '/live', label: 'Terminal', glyph: '▶' },
  // TODO(kompany-world): point the embed at the world mount once FastAPI serves
  // it (kompany-world dist/ at `/world/`). No route exists today — keep disabled.
  { path: null, label: 'World', glyph: '◴', disabled: true, disabledTag: 'unmounted' },
];

export const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Workspace',
    items: [
      { path: '/', label: 'Board', glyph: '▦' },
      { path: '/activity', label: 'Activity', glyph: '≣' },
      { path: '/projects', label: 'Projects', glyph: '◧' },
      { path: '/autopilot', label: 'Autopilot', glyph: '◆' },
      { path: '/agents', label: 'Agents', glyph: '☷' },
      { path: '/usage', label: 'Usage', glyph: '∑' },
    ],
  },
  {
    label: 'Configure',
    items: [
      { path: '/runtimes', label: 'Runtimes', glyph: '⚙' },
      { path: null, label: 'Skills', glyph: '✦', disabled: true },
      { path: '/settings', label: 'Settings', glyph: '⚒' },
      // Top-level "Live" shortcut → the embedded `/live` pane (cyberpunk
      // terminal in an iframe). Internal route so the founder can always nav
      // back. The full live menu lives in LIVE_ITEMS.
      { path: '/live', label: 'Live', glyph: '↗' },
    ],
  },
  {
    label: 'Live',
    items: LIVE_ITEMS,
  },
];
