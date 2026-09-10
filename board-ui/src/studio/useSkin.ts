// Studio skin = UI preference `skin` (GET/PATCH /preferences). Applied as
// data-skin on <html> so every shell token follows it. Default: indigo.

import { useCallback, useEffect, useState } from 'react';

export const SKINS: { id: string; label: string; hint: string }[] = [
  { id: 'indigo', label: 'Indigo Night', hint: 'near-black + one indigo · measured from Linear' },
  { id: 'cyberpunk', label: 'Cyberpunk', hint: 'Kompany green-on-black, mono everywhere' },
  { id: 'signal', label: 'Signal', hint: 'near-black + signal red / mint · measured from Raycast' },
  { id: 'paper', label: 'Paper Blurple', hint: 'light · white + navy ink + blurple · measured from Stripe' },
  { id: 'blueprint', label: 'Blueprint', hint: 'light · monochrome, blue only for events · measured from Vercel' },
];

const KEY = 'kompany.skin';

export function applySkin(id: string): void {
  const valid = SKINS.some((s) => s.id === id) ? id : 'indigo';
  document.documentElement.setAttribute('data-skin', valid);
  try { localStorage.setItem(KEY, valid); } catch { /* private mode */ }
}

export function bootSkin(): void {
  let cached = 'indigo';
  try { cached = localStorage.getItem(KEY) ?? 'indigo'; } catch { /* ignore */ }
  applySkin(cached);
  fetch('/preferences', { headers: { accept: 'application/json' } })
    .then((r) => (r.ok ? r.json() : null))
    .then((p: { skin?: string } | null) => { if (p?.skin) applySkin(p.skin); })
    .catch(() => undefined);
}

export function useSkin() {
  const [skin, setSkinState] = useState(() => document.documentElement.getAttribute('data-skin') ?? 'indigo');
  useEffect(() => {
    const obs = new MutationObserver(() => setSkinState(document.documentElement.getAttribute('data-skin') ?? 'indigo'));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-skin'] });
    return () => obs.disconnect();
  }, []);
  const setSkin = useCallback(async (id: string) => {
    applySkin(id);
    const res = await fetch('/preferences', {
      method: 'PATCH', headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({ skin: id }),
    });
    if (!res.ok) throw new Error(`PATCH /preferences → ${res.status}`);
  }, []);
  return { skin, setSkin };
}
