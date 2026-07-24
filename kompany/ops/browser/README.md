# Kompany browser infrastructure

Shared headed-browser infrastructure for integrations that target bot-protected
sites (LinkedIn, X, Weibo, Xiaohongshu, Douyu, …). One Brave instance per
integration, all headed on a shared Xvfb.

## Provenance

This directory replaces the standalone `Fei2-Labs/kompany-browser` repo
(deprecated/archived). It's released at the same version/tag as Core (see
the "Package ops/browser tarball" step in `release.yml`), because this
infra is tightly coupled to Core's `browser_tools.py` CDP contract, not a
pluggable business-logic unit like Pro's workflows/souls.

**Deployment vs. source location**: this is the *release-packaging source*.
At runtime it's extracted to `~/kompany-browser` on the target machine (the
app scripts/`lib-browser.mjs` all resolve config via `$HOME/kompany-browser`,
so `~/kompany-browser` is the fixed deployment convention — analogous to how
Core itself deploys to `/opt` rather than running from its own repo
checkout). The `systemd/*.service` unit files use the literal absolute path
`/home/kosonen/...` because systemd units can't expand `~`/`$HOME` — treat
them as a per-deployment template and substitute the actual user/home path
when installing on a different machine.

App files (`lib-browser.mjs`, `*.sh`, `config/*.env`) were verified
byte-identical (SHA256) against the live `/home/kosonen/kompany-browser` on
`kosonen-server` at migration time — no drift. The `systemd/*.service` unit
files, however, **did** have real drift from the previously-reconstructed
templates in the standalone repo (missing `OnFailure=` alert hook, wrong
`Requires`/`After`, wrong `RestartSec`, extra fields that were never
actually installed) — these three files were replaced with a verbatim copy
of what's actually installed and running at `/etc/systemd/system/` on
`kosonen-server`.

## Why headed, never headless

Target sites run aggressive bot risk control. Headless Chromium is flagged on
sight via: `navigator.webdriver`, missing WebGL context, no real composited
layout, no foreground tab focus, CDP-protocol fingerprints. A headless browser
gets rate-limited, captcha-walled, or session-banned within minutes.

This infrastructure **refuses to launch a headless browser**. Every instance
runs headed on the shared Xvfb :99 with a real 1920x1200 framebuffer, real
rendering, and foreground-tab focus. `start-browser.sh` errors out (exit 3) if:
- `EXTRA_FLAGS` contains any `--headless*` flag
- `DISPLAY` is unset
- Xvfb is not reachable and cannot be started

Every page driven through `lib-browser.mjs` is a foreground tab with
`bringToFront()` — background tabs on these sites render empty/throttled and
look bot-like.

## Layout

```
~/kompany-browser/
├── start-browser.sh <name>   # headed Brave launcher (systemd ExecStart)
├── stop-browser.sh <name>    # clean stop: SIGTERM + 30s cookie flush, then SIGKILL
├── lib-browser.mjs           # connectBrowser(name) + gotoRendered() (playwright-core)
├── status.sh                 # JSON status of all integrations
├── config/
│   ├── linkedin.env          # port 9335, profile ~/Business/linkedin-growth/li-chrome
│   ├── x.env                 # port 9336 (not yet active)
│   └── weibo.env             # port 9337 (not yet active)
└── profiles/                 # user-data-dirs for non-legacy integrations
```

## Add a new integration (zero code)

1. `cp config/x.env config/<name>.env` — set a unique `KOMPANY_BROWSER_PORT`,
   `USER_DATA_DIR`, `LANG`/`ACCEPT_LANG`.
2. `systemctl enable --now kompany-browser@<name>`
3. Log in to the site once (VNC/SSH-X11 to `:99`, or open the CDP browser from
   your laptop via `ssh -L <port>:127.0.0.1:<port>` and visit
   `http://127.0.0.1:<port>`). The session persists in the user-data-dir.
4. In your worker script: `import { connectBrowser } from "~/kompany-browser/lib-browser.mjs"`
   → `const { b, ctx, page } = await connectBrowser("<name>")`.

## Per-integration isolation

Each integration gets its own Brave process, port, user-data-dir, and systemd
unit (`kompany-browser@<name>.service`). Cookie/profile isolation prevents
cross-site fingerprint correlation. Fault isolation: X crashing does not take
down LinkedIn. Independent restart: `systemctl restart kompany-browser@x`.

## When NOT to use this

- **Public pages, no login needed** — use ephemeral `playwright` headless
  directly. This infra is for logged-in sessions on bot-protected sites.
- **Site has a real API** (Slack, Notion, Linear, GitHub) — use the API.
  Browser automation is the last resort for sites that gate behind bot risk
  control and have no usable API.

## Shared Xvfb

`xvfb.service` provides `:99` at 1920x1200x24. All Brave instances draw to it.
Xvfb does not care how many clients connect. Do not run headless just because
there is no physical monitor — Xvfb IS the "monitor".
