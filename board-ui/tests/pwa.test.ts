import { describe, expect, it } from 'vitest';
import { shouldRegisterServiceWorker } from '../src/pwa';
import { MOBILE_TABS, NAV_GROUPS, TOP_ITEMS } from '../src/nav';

describe('service worker registration guard', () => {
  const base = { hasServiceWorker: true, isSecureContext: true, isTauri: false };
  it('registers on a secure web origin', () => {
    expect(shouldRegisterServiceWorker(base)).toBe(true);
  });
  it('skips the Tauri WebView', () => {
    expect(shouldRegisterServiceWorker({ ...base, isTauri: true })).toBe(false);
  });
  it('skips insecure origins and browsers without service workers', () => {
    expect(shouldRegisterServiceWorker({ ...base, isSecureContext: false })).toBe(false);
    expect(shouldRegisterServiceWorker({ ...base, hasServiceWorker: false })).toBe(false);
  });
});

describe('mobile tabs', () => {
  it('cover the four primary jobs and only point at real routes', () => {
    expect(MOBILE_TABS.map((t) => t.label)).toEqual(['Studio', 'Needs You', 'kompany>', 'Board']);
    const routes = new Set(
      [...TOP_ITEMS, ...NAV_GROUPS.flatMap((g) => g.items)].map((i) => i.path).filter(Boolean),
    );
    for (const tab of MOBILE_TABS) expect(routes.has(tab.path)).toBe(true);
  });
});
