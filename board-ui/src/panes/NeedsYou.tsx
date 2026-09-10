// /needs-you — the founder's inbox, grouped by the three jobs: money,
// connect accounts, decisions. Same cards as the Studio right rail.

import type { UseChannel } from '../channel/useChannel';
import { NeedsYouCard } from '../studio/NeedsYouCard';
import { countByKind, KIND_ORDER, type NeedsKind } from '../studio/needsYou';
import { useNeedsYou } from '../studio/useNeedsYou';
import { PaneShell } from './PaneShell';

const HEADINGS: Record<NeedsKind, { title: string; hint: string }> = {
  money: { title: 'Money', hint: 'Spend the team cannot authorise alone.' },
  account: { title: 'Connect accounts', hint: 'Work is blocked until an integration is connected.' },
  decision: { title: 'Decisions', hint: 'Gates, escalations and choices only you can make.' },
};

export function NeedsYou({ channel }: { channel: UseChannel }) {
  const ny = useNeedsYou();
  const counts = countByKind(ny.items);
  return (
    <PaneShell
      title="Needs You"
      subtitle="Money, accounts, decisions. Everything else the team does itself."
      state={ny.state}
      error={ny.error}
      empty={ny.items.length === 0}
      emptyText="Nothing needs you. The team is working."
    >
      <div className="ny-groups">
        {KIND_ORDER.filter((k) => counts[k] > 0).map((k) => (
          <section className="ny-group" key={k}>
            <header className="studio__head">
              {HEADINGS[k].title} <b>{counts[k]}</b>
            </header>
            <p className="rail__hint">{HEADINGS[k].hint}</p>
            {ny.items
              .filter((i) => i.kind === k)
              .map((i) => (
                <NeedsYouCard key={i.id} item={i} channel={channel} onResolved={ny.resolved} />
              ))}
          </section>
        ))}
      </div>
    </PaneShell>
  );
}
