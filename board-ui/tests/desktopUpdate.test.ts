import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { describe, expect, it } from 'vitest';

// Exercise the actual trusted-shell script without giving engine content IPC.
const html = readFileSync(new URL('../../tauri/src-tauri/dist/index.html', import.meta.url), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)![1]!;

function shell(initial: Record<string, unknown>) {
  const elements = new Map<string, ReturnType<typeof element>>();
  function element() {
    return {
      hidden: false, disabled: false, textContent: '', title: '', value: 0, max: 1,
      attributes: {} as Record<string, string>,
      classList: { toggle() {}, contains() { return false; } },
      append() {}, replaceChildren() {}, reset() {},
      style: {},
      setAttribute(name: string, value: string) { this.attributes[name] = value; },
      removeAttribute(name: string) { delete this.attributes[name]; if (name === 'value') this.value = NaN; },
      listeners: {} as Record<string, () => Promise<void>>,
      addEventListener(name: string, callback: () => Promise<void>) { this.listeners[name] = callback; },
    };
  }
  const get = (id: string) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id)!;
  };
  let status = initial;
  let poll = async () => {};
  vm.runInNewContext(script, {
    window: { __TAURI_INTERNALS__: { invoke: async (command: string) => command === 'update_status'
      ? status : { active_id: '', targets: [] } } },
    document: { getElementById: get, createElement: element, body: element() },
    setInterval(callback: () => Promise<void>, interval: number) {
      expect(interval).toBe(1000);
      poll = callback;
    },
  });
  return { get, async show(next: Record<string, unknown>) { status = next; await poll(); } };
}

describe('desktop shell update progress', () => {
  it('displays measured bytes and percentage during download', async () => {
    const app = shell({ state: 'idle' });
    await app.show({ state: 'downloading', downloaded_bytes: 524288, total_bytes: 1048576 });
    expect(app.get('update-label').textContent).toBe('Downloading 50% · 0.5 MB / 1.0 MB');
    expect(app.get('update-meter').value).toBe(524288);
    expect(app.get('check-update').disabled).toBe(true);
    expect(app.get('update').hidden).toBe(true);
  });

  it('keeps missing lengths indeterminate and does not announce installation early', async () => {
    const app = shell({ state: 'idle' });
    await app.show({ state: 'downloading', downloaded_bytes: 1048576, total_bytes: null });
    expect(app.get('update-meter').value).toBeNaN();
    expect(app.get('update-label').textContent).toBe('Downloading · 1.0 MB');
    await app.show({ state: 'verifying' });
    expect(app.get('update-label').textContent).toBe('Verifying signature…');
    expect(app.get('update-meter').value).toBeNaN();
    await app.show({ state: 'installing' });
    expect(app.get('update-label').textContent).toBe('Installing update…');
    expect(app.get('update').hidden).toBe(true);
  });

  it('keeps a manual check disabled while the updater is active', async () => {
    const app = shell({ state: 'checking' });
    await app.get('check-update').listeners.click!();
    expect(app.get('check-update').disabled).toBe(true);
    await app.show({ state: 'idle' });
    expect(app.get('check-update').disabled).toBe(false);
  });

  it('only offers restart when ready and exposes errors for retry', async () => {
    const app = shell({ state: 'idle' });
    await app.show({ state: 'ready', ready_version: '0.2.2' });
    expect(app.get('update-progress').hidden).toBe(true);
    expect(app.get('update').textContent).toBe('Restart to update (0.2.2)');
    expect(app.get('check-update').hidden).toBe(true);
    await app.show({ state: 'error', error: 'Signature verification failed' });
    expect(app.get('update').hidden).toBe(true);
    expect(app.get('update-error').textContent).toBe('Signature verification failed');
    expect(app.get('check-update').disabled).toBe(false);
  });
});
