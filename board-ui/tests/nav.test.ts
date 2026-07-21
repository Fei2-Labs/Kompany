import { describe, expect, it } from 'vitest';
import { LIVE_ITEMS, NAV_GROUPS, TOP_ITEMS } from '../src/nav';

describe('nav model', () => {
  it('exposes the Talk-to-CEO and Needs-You top items', () => {
    const labels = TOP_ITEMS.map((i) => i.label);
    expect(labels).toContain('kompany>');
    expect(labels).toContain('Needs You');
  });

  it('groups Workspace, Configure and Live', () => {
    const labels = NAV_GROUPS.map((g) => g.label);
    expect(labels).toEqual(['Workspace', 'Configure', 'Live']);
  });

  it('embeds the cyberpunk terminal via an internal /live route (no stranding href)', () => {
    const terminal = LIVE_ITEMS.find((i) => i.label === 'Terminal');
    // Must be an internal route, NOT a raw href that navigates the WebView away
    // with no way back.
    expect(terminal?.path).toBe('/live');
    expect(terminal?.href).toBeUndefined();
    const world = LIVE_ITEMS.find((i) => i.label === 'World');
    // No FastAPI route for kompany-world yet — must be a disabled stub, not a
    // fabricated href.
    expect(world?.disabled).toBe(true);
    expect(world?.href).toBeUndefined();
  });

  it('keeps a Live shortcut routing to the embedded /live pane', () => {
    const configure = NAV_GROUPS.find((g) => g.label === 'Configure');
    const live = configure?.items.find((i) => i.label === 'Live');
    expect(live?.path).toBe('/live');
    expect(live?.href).toBeUndefined();
  });

  it('marks Skills as a disabled "soon" slot', () => {
    const configure = NAV_GROUPS.find((g) => g.label === 'Configure');
    const skills = configure?.items.find((i) => i.label === 'Skills');
    expect(skills?.disabled).toBe(true);
  });
});
