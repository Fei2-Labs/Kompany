// Appearance — the Studio skin (UI preference `skin`). Five measured token
// sets; applies instantly and persists server-side.

import { useState } from 'react';
import { SKINS, useSkin } from '../../studio/useSkin';

export function AppearanceCard() {
  const { skin, setSkin } = useSkin();
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);
  const onChange = async (id: string) => {
    setResult(null);
    try { await setSkin(id); setResult({ ok: true, detail: `saved · ${id}` }); }
    catch (err) { setResult({ ok: false, detail: err instanceof Error ? err.message : 'save failed' }); }
  };
  return (
    <section className="settings__card">
      <header className="settings__card-head"><h2 className="settings__card-title">Appearance</h2></header>
      <p className="settings__hint">Skin for the whole app. Colours are measured from real products, not guessed; the terminal keeps its own theme.</p>
      <label className="settings__field">
        <span className="settings__label">Skin</span>
        <select className="settings__select" value={skin} onChange={(e) => void onChange(e.target.value)} aria-label="Skin">
          {SKINS.map((s) => <option key={s.id} value={s.id}>{s.label} — {s.hint}</option>)}
        </select>
      </label>
      {result && <span className={'settings__result ' + (result.ok ? 'settings__result--ok' : 'settings__result--err')}>{result.ok ? '✓ ' : '✗ '}{result.detail}</span>}
    </section>
  );
}
