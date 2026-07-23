// Browser card — CDP endpoint selection for the agentic loop's browser
// tools. A real browser (Brave/Chrome/Edge) runs with
// --remote-debugging-port=N and a dedicated --user-data-dir so the agent
// reuses the logged-in profile. This card lets them pick which endpoint
// to connect to, auto-detect running browsers, and test the connection.

import { useCallback, useEffect, useState } from 'react';
import {
  getBrowserConfig,
  setBrowserConfig,
  probeBrowsers,
  type BrowserConfig,
  type BrowserProbeResult,
} from '../../api/client';
import { useAsync } from '../useAsync';

const configLoader = (signal?: AbortSignal) => getBrowserConfig(signal);

export function BrowserCard() {
  const config = useAsync<BrowserConfig>(configLoader);

  const [endpoint, setEndpoint] = useState('');
  const [result, setResult] = useState<string | null>(null);
  const [resultOk, setResultOk] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<BrowserProbeResult | null>(null);

  useEffect(() => {
    if (config.state === 'ready' && config.data) {
      setEndpoint(config.data.cdp_endpoint || '');
    }
  }, [config.data, config.state]);

  const onSave = useCallback(async () => {
    if (!endpoint.trim()) {
      setResult('CDP endpoint is required.');
      setResultOk(false);
      return;
    }
    setResult(null);
    setSubmitting(true);
    try {
      const r = await setBrowserConfig(endpoint.trim());
      if (r.ok) {
        setResult(
          r.connected
            ? `✓ Connected — ${r.browser_type || 'browser detected'}`
            : 'Saved, but the backend could not reach a browser at that endpoint.',
        );
        setResultOk(Boolean(r.connected));
        config.reload();
      } else {
        setResult(r.detail || 'Failed to save.');
        setResultOk(false);
      }
    } catch (err) {
      setResult(err instanceof Error ? err.message : 'save failed');
      setResultOk(false);
    } finally {
      setSubmitting(false);
    }
  }, [endpoint, config]);

  const onProbe = useCallback(async () => {
    setProbing(true);
    setProbeResult(null);
    try {
      const r = await probeBrowsers();
      setProbeResult(r);
    } catch {
      setProbeResult({ browsers: [] });
    } finally {
      setProbing(false);
    }
  }, []);

  const cfg = config.data;

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Browser</h2>
        <span
          className={
            'settings__badge ' +
            (cfg?.connected
              ? 'settings__badge--ok'
              : cfg?.playwright_installed
                ? 'settings__badge--off'
                : 'settings__badge--off')
          }
        >
          {cfg?.connected
            ? 'connected'
            : cfg?.playwright_installed
              ? 'not connected'
              : 'playwright missing'}
        </span>
      </header>

      <p className="settings__hint">
        The CEO agent connects from the Kompany backend host to a browser over
        CDP so login sessions can be preserved. Start a Chromium-based browser,
        for example with{' '}
        <code>
          brave-browser --remote-debugging-port=9223 --user-data-dir=~/.
          kompany/browser-profile --no-sandbox --remote-allow-origins=*
        </code>
        , then set the endpoint below. Auto-detect scans the backend host;
        127.0.0.1 refers to that machine, not the device viewing this panel. If
        no browser is running, the agent falls back to headless Chromium
        (public web only, no logins).
      </p>

      {cfg?.browser_type && cfg.connected && (
        <p className="settings__meta">
          backend detected: {cfg.browser_type}
        </p>
      )}

      <label className="settings__field">
        <span className="settings__label">
          CDP endpoint (reachable from backend)
        </span>
        <input
          className="settings__input"
          type="text"
          value={endpoint}
          placeholder="http://127.0.0.1:9223"
          onChange={(e) => setEndpoint(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
      </label>

      {probeResult && (
        <div className="settings__probe-results">
          {probeResult.browsers.length === 0 ? (
            <p className="settings__meta">
              No browsers found on the backend host&apos;s common CDP ports
              (9222, 9223, 9335, …).
            </p>
          ) : (
            <>
              <p className="settings__meta">Detected on backend host:</p>
              {probeResult.browsers.map((b) => (
                <button
                  key={b.port}
                  className="btn btn--ghost btn--sm"
                  onClick={() => setEndpoint(b.endpoint)}
                >
                  {b.browser_type || 'browser'} · port {b.port}
                </button>
              ))}
            </>
          )}
        </div>
      )}

      <div className="settings__actions">
        <button
          className="btn btn--primary btn--sm"
          onClick={onSave}
          disabled={submitting}
        >
          {submitting ? 'Saving…' : 'Save + Test'}
        </button>
        <button
          className="btn btn--ghost btn--sm"
          onClick={onProbe}
          disabled={probing}
        >
          {probing ? 'Scanning…' : 'Auto-detect'}
        </button>
        {result && (
          <span
            className={
              'settings__result ' +
              (resultOk ? 'settings__result--ok' : 'settings__result--err')
            }
          >
            {result}
          </span>
        )}
      </div>
    </section>
  );
}
