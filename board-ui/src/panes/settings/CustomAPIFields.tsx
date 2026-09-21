import { useEffect, useState } from 'react';
import { getCustomLLMSetting, setCustomLLMSetting } from '../../api/client';
import { useAsync } from '../useAsync';

const connectionLoader = (signal?: AbortSignal) => getCustomLLMSetting(signal);

export function CustomAPIFields({ onSaved }: { onSaved: () => void }) {
  const connection = useAsync(connectionLoader);
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  useEffect(() => {
    if (connection.data) setBaseUrl(connection.data.base_url);
  }, [connection.data]);

  async function save() {
    setSaving(true);
    setResult(null);
    try {
      const saved = await setCustomLLMSetting({ base_url: baseUrl.trim(), api_key: apiKey });
      setApiKey('');
      setResult({ ok: !saved.warning, detail: saved.warning || 'API connection saved. New calls use it now.' });
      connection.reload();
      onSaved();
    } catch {
      // Never render a response body that might contain submitted credentials.
      setResult({ ok: false, detail: 'Not saved. Check the URL and enter the key again if the URL changed.' });
    } finally {
      setSaving(false);
    }
  }

  return (
    <fieldset disabled={saving || connection.state !== 'ready'} className="settings__api-fields">
      <legend className="settings__label">Custom API connection</legend>
      {connection.state === 'loading' && <p className="settings__meta">Loading connection…</p>}
      {connection.state === 'error' && <p role="alert">Could not load the API connection. Reload Settings to retry.</p>}
      <label className="settings__field">
        <span className="settings__label">Base URL</span>
        <input className="settings__input" type="url" value={baseUrl}
          placeholder="https://your-provider.example/v1" autoComplete="off"
          onChange={(event) => setBaseUrl(event.target.value)} />
      </label>
      <label className="settings__field">
        <span className="settings__label">API key</span>
        <input className="settings__input" type="password" value={apiKey}
          autoComplete="new-password" spellCheck={false}
          placeholder={connection.data?.api_key_configured ? 'Configured — leave blank to keep' : 'Enter API key'}
          onChange={(event) => setApiKey(event.target.value)} />
      </label>
      <p className="settings__hint">
        {connection.data?.api_key_configured ? 'A key is configured. ' : 'No key configured. '}
        Leave the key blank to keep it. Enter it again when changing the URL.
        Saving checks the model list, not inference.
      </p>
      <div className="settings__actions">
        <button className="btn btn--primary btn--sm" type="button" onClick={save} disabled={!baseUrl.trim()}>
          {saving ? 'Saving…' : 'Save API connection'}
        </button>
      </div>
      {result && <p role="status" className={'settings__result ' + (result.ok ? 'settings__result--ok' : 'settings__result--err')}>
        {result.detail}
      </p>}
    </fieldset>
  );
}
