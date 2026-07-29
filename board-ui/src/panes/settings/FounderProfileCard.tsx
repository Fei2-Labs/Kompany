// Founder profile card — how the team addresses + talks to the founder.
// Style shapes phrasing only; it never softens an honest assessment.

import { useCallback, useEffect, useState } from 'react';
import { getFounderProfile, setFounderProfile, type FounderProfile } from '../../api/client';
import { useAsync } from '../useAsync';

const FIELDS: Array<{ key: keyof FounderProfile; label: string; placeholder: string }> = [
  { key: 'address', label: 'Address you as', placeholder: 'Clare / boss / founder' },
  { key: 'pronouns', label: 'Pronouns (optional)', placeholder: 'she/her' },
  { key: 'comms_style', label: 'Comms style', placeholder: 'terse, direct, no fluff' },
  { key: 'language', label: 'Language', placeholder: 'zh / en' },
  { key: 'working_hours', label: 'Working hours (optional)', placeholder: '09:00-18:00' },
  { key: 'timezone', label: 'Timezone (optional)', placeholder: 'Europe/Stockholm' },
  {
    key: 'risk_tolerance',
    label: 'Risk tolerance (optional)',
    placeholder: 'conservative / balanced / aggressive',
  },
];

const profileLoader = (signal?: AbortSignal) => getFounderProfile(signal);

export function FounderProfileCard() {
  const profile = useAsync<FounderProfile | null>(profileLoader);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  useEffect(() => {
    if (profile.state === 'ready') {
      const next: Record<string, string> = {};
      for (const f of FIELDS) next[f.key] = profile.data?.[f.key] ?? '';
      setValues(next);
    }
  }, [profile.data, profile.state]);

  const onSave = useCallback(async () => {
    const body: Record<string, string> = {};
    let any = false;
    for (const f of FIELDS) {
      const v = (values[f.key] ?? '').trim();
      if (v) {
        body[f.key] = v;
        any = true;
      }
    }
    setSaving(true);
    setResult(null);
    try {
      if (!any) {
        await setFounderProfile({ clear: true });
        setResult({ ok: true, detail: 'profile cleared' });
      } else {
        await setFounderProfile(body);
        setResult({ ok: true, detail: 'profile saved' });
      }
      profile.reload();
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'save failed',
      });
    } finally {
      setSaving(false);
    }
  }, [values, profile]);

  return (
    <section className="settings__card">
      <header className="settings__card-head">
        <h2 className="settings__card-title">Founder Profile</h2>
      </header>
      <p className="settings__hint">
        How the team addresses + talks to you. Style shapes phrasing only —
        it never softens an honest assessment.
      </p>

      <div className="settings__grid">
        {FIELDS.map((f) => (
          <label className="settings__field" key={f.key}>
            <span className="settings__label">{f.label}</span>
            <input
              className="settings__input"
              value={values[f.key] ?? ''}
              placeholder={f.placeholder}
              onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              autoComplete="off"
            />
          </label>
        ))}
      </div>

      <div className="settings__actions">
        <button className="btn btn--primary btn--sm" onClick={onSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save profile'}
        </button>
        {result && (
          <span
            className={
              'settings__result ' +
              (result.ok ? 'settings__result--ok' : 'settings__result--err')
            }
          >
            {result.ok ? '✓ ' : '✗ '}
            {result.detail}
          </span>
        )}
      </div>
    </section>
  );
}
