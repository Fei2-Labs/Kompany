# kompany-board

React + Vite + TypeScript SPA — Kompany's operations board. Served by the
FastAPI engine at `/` (the cyberpunk terminal stays at `/ui/`).

## Stack

- Vite 7, React 18, TypeScript 5.8, ESLint flat config, Vitest.
- Package manager: **npm** (matches the rest of the monorepo).

## Develop

```bash
npm install
npm run dev        # Vite dev server; proxies API routes to the engine
```

The dev server proxies the engine's REST + SSE routes (`/projects`,
`/inbox`, `/events`, `/channel`, …) to `KOMPANY_CORE_ORIGIN`
(default `http://127.0.0.1:8000`). Start the engine separately:

```bash
cd ../kompany && python -m kompany.interfaces.cli serve   # or your usual serve cmd
```

## Build

```bash
npm run build
```

`vite build` emits into **`../kompany/src/kompany/board_ui/dist/`** (inside
the Python package) so the wheel and the PyInstaller desktop bundle ship
the assets. FastAPI serves that directory at `/` via a SPA catch-all
registered after all API routers (see `interfaces/api.py`).

## Nav map

Left rail (Linear-style), grouped:

- **Top** — `kompany>` (Talk-to-CEO), Needs You
- **Workspace** — Board · Activity · Projects · Autopilot · Agents · Usage
- **Configure** — Runtimes · Skills _(disabled, "soon")_ · Settings · Live↗ (`/ui/`)
- **Live** — Terminal (`/ui/`, cyberpunk) · World _(disabled stub — kompany-world
  has no FastAPI mount yet; wire its href once `dist/` is served, e.g. `/world/`)_

Panes & sources (all read-only except the Board's Needs-You actions, the CEO
channel, and the engine runtime controls):

| Pane | Source(s) |
|---|---|
| Board | `/projects?include_draft=1` + `/inbox` + `/episodes` + `/status`, live via `/events` |
| Board header | runtime strip — `GET /runtime`, `POST /runtime/suspend\|/resume`, `POST /heartbeat` |
| Projects | `GET /projects?include_draft=1` |
| Autopilot | `GET /status` ticker + `GET /observability` recent ticks (refetch on `daemon.tick`) |
| Agents | `GET /agents/status` (11 C-suite roles) |
| Usage | `GET /llm/spend/summary` + `GET /status` (live `llm.spend`) |
| Runtimes | `GET /runtime` + suspend/resume/heartbeat |

The Board's empty-state ("Set up your company") links to the existing
onboarding wizard at `/ui/onboarding` (served by the cyberpunk web_ui).

## Autonomy note

This board is **act-where-needed**, not a project-management tool. There is
**no manual task assignment**, no drag-between-columns, no squad editor. Agents
self-assign; the CEO routes work. The founder acts only on Needs-You items
(approve / reject / revise / snooze), steers via the CEO channel, and controls
the engine runtime. The Agents, Projects, Usage and Autopilot panes are
deliberately read-only.

## Checks

```bash
npm run typecheck
npm run lint
npm run test
```

## Packaging note (desktop bundle)

The PyInstaller sidecar bundles the package via `--collect-all kompany`,
which only picks up files declared in `pyproject.toml`'s wheel/sdist
`include` globs. `src/kompany/board_ui/dist/**/*` is listed there.

**The board must be built before the desktop bundle is built**, so the
`dist/` is on disk for `--collect-all kompany`. `kompany/build_desktop.sh`
runs `npm --prefix board-ui ci && npm --prefix board-ui run build` as
Stage 0 before building the sidecar. If you build the sidecar directly
(`build_sidecar.sh`), run `npm --prefix board-ui run build` first.
