// lib-browser.mjs — shared Kompany browser CDP helpers.
//
// === HARD RULE: HEADED + FOREGROUND TAB, NEVER headless ===
// Target sites (LinkedIn, X, Weibo, Xiaohongshu, Douyu, …) run aggressive bot
// risk control. Headless Chromium is flagged on sight (navigator.webdriver,
// missing WebGL, no real layout, no foreground focus). The backing Brave is
// always headed on Xvfb :99 (enforced by start-browser.sh). On top of that,
// every page we drive MUST be a foreground tab with bringToFront() — driven
// background tabs on these sites render empty/throttled and look bot-like.
//
// One Brave instance per integration, each on its own port + user-data-dir
// (cookie/profile isolation, fault isolation, independent restart).
// Config: ~/kompany-browser/config/<name>.env
//   KOMPANY_BROWSER_PORT=9335
//   USER_DATA_DIR=/home/kosonen/.../li-chrome
//   PROFILE_DIR=Default
//   LANG=en-US
//   WINDOW_SIZE=1920x1200
import { chromium } from "playwright-core";
import fs from "fs";
import path from "path";
import os from "os";

export function configPath(name) {
  return path.join(os.homedir(), "kompany-browser/config", `${name}.env`);
}

export function loadConfig(name) {
  const file = configPath(name);
  if (!fs.existsSync(file)) throw new Error(`missing browser config: ${file}`);
  const cfg = {};
  for (const line of fs.readFileSync(file, "utf8").split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const i = t.indexOf("=");
    if (i < 0) continue;
    cfg[t.slice(0, i).trim()] = t.slice(i + 1).trim();
  }
  return cfg;
}

// Connect to an integration already-running Brave over CDP and open a fresh
// FOREGROUND page. Anti-bot: bringToFront() is mandatory — background tabs on
// bot-protected sites render empty/throttled. Caller is responsible for any
// integration-specific tab cleanup (e.g. closing stale linkedin.com tabs)
// before using the page.
export async function connectBrowser(name) {
  const cfg = loadConfig(name);
  const port = cfg.KOMPANY_BROWSER_PORT;
  if (!port) throw new Error(`KOMPANY_BROWSER_PORT missing in config for ${name}`);
  const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`); // 127.0.0.1, not localhost (IPv6 ::1 refuses)
  const ctx = b.contexts()[0];
  const page = await ctx.newPage();
  await page.bringToFront().catch(() => {});
  return { b, ctx, page, cfg, port };
}

// navigate + wait until `check(page)` returns truthy, retrying reloads.
// Anti-bot: bringToFront() before every navigation attempt, and scroll the
// feed between reloads — these sites lazy-render and a still viewport looks
// bot-like. Returns "OK" | "NOT_LOGGED_IN" | "EMPTY".
export async function gotoRendered(page, url, check, tries = 4) {
  for (let i = 0; i < tries; i++) {
    await page.bringToFront().catch(() => {});
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
    if (page.url().includes("/login")) return "NOT_LOGGED_IN";
    await page.waitForTimeout(4500 + i * 1500);
    for (let s = 0; s < 5; s++) { await page.mouse.wheel(0, 2200); await page.waitForTimeout(1500); }
    if (await page.evaluate(check).catch(() => false)) return "OK";
  }
  return "EMPTY";
}
