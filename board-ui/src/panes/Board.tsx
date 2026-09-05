// The operations board: metrics strip + four columns (Backlog / In-Flight /
// Needs-You / Done) composed from /projects, /inbox, /episodes, /status, live
// via the shared SSE dispatcher. Read-only display (PR2); clicking a card
// opens the inspect drawer (PR3 action seam).

import { useMemo, useState } from 'react';
import { useBoardData } from '../board/useBoardData';
import {
  buildBacklog,
  buildDone,
  buildInFlight,
  buildNeedsYou,
} from '../board/classify';
import type { ProjectDetail } from '../api/types';
import { MetricsStrip } from '../board/MetricsStrip';
import { RuntimeStrip } from '../runtime/RuntimeStrip';
import { Column } from '../board/Column';
import { Card, type BoardCard } from '../board/Card';
import { InspectDrawer } from '../board/InspectDrawer';
import { ProgressSummary } from '../board/ProgressSummary';

/** Worst load-state across the sources a column depends on. */
function worstState(
  ...states: ('loading' | 'ready' | 'error')[]
): 'loading' | 'ready' | 'error' {
  if (states.includes('error')) return 'error';
  if (states.includes('loading')) return 'loading';
  return 'ready';
}

export function Board() {
  const data = useBoardData();
  const [selected, setSelected] = useState<BoardCard | null>(null);

  const detailList = useMemo<ProjectDetail[]>(
    () => [...data.details.values()],
    [data.details],
  );

  const backlog = useMemo(
    () => buildBacklog(data.withDraft.data),
    [data.withDraft.data],
  );
  const inFlight = useMemo(
    () => buildInFlight(data.active.data, data.details),
    [data.active.data, data.details],
  );
  const needsYou = useMemo(
    () => buildNeedsYou(data.inbox.data, detailList),
    [data.inbox.data, detailList],
  );
  const done = useMemo(
    () => buildDone(data.withDraft.data, data.episodes.data),
    [data.withDraft.data, data.episodes.data],
  );

  // Whole-board empty state (no projects at all). The onboarding wiring is
  // PR4 — for now this is the hook (a clear empty surface, no native dialog).
  const sourcesReady =
    data.withDraft.state === 'ready' &&
    data.active.state === 'ready' &&
    data.inbox.state === 'ready';
  const boardEmpty =
    sourcesReady &&
    data.withDraft.data.length === 0 &&
    data.inbox.data.length === 0;

  const selectedId = selectedKey(selected);

  return (
    <section className="board">
      <header className="board__header">
        <div>
          <h1 className="board__title">Board</h1>
          <p className="board__subtitle">A quick view of what Kompany is doing and what needs you.</p>
        </div>
        <RuntimeStrip />
      </header>

      <MetricsStrip status={data.status} spendDelta={data.spendDelta} />

      <ProgressSummary
        status={data.status.data}
        activeProjects={data.active.data}
        needsYouCount={needsYou.length}
      />

      {boardEmpty ? (
        <div className="board__empty">
          <p className="board__empty-title">Set up your company</p>
          <p>
            No company or projects yet. Run the onboarding wizard so the CEO has
            a goal, budget, and deadline to work toward.
          </p>
          {/* Plain anchor — the onboarding wizard lives in the cyberpunk UI
              (web_ui/onboarding.html, aliased at /ui/onboarding). No
              window.open / native dialog (Tauri WebView). */}
          <a className="btn btn--primary" href="/ui/onboarding">
            Set up your company
          </a>
        </div>
      ) : (
        <div className="board__columns">
          <Column
            title="Backlog"
            count={backlog.length}
            state={data.withDraft.state}
            error={data.withDraft.error}
            emptyText="No drafts staged."
          >
            {backlog.map((project) => {
              const card: BoardCard = { column: 'backlog', project };
              return (
                <Card
                  key={project.id}
                  card={card}
                  selected={selectedId === keyOf(card)}
                  onSelect={setSelected}
                />
              );
            })}
          </Column>

          <Column
            title="In-Flight"
            count={inFlight.length}
            state={data.active.state}
            error={data.active.error}
            emptyText="Nothing in flight."
          >
            {inFlight.map((c) => {
              const card: BoardCard = { column: 'inflight', card: c };
              return (
                <Card
                  key={c.project.id}
                  card={card}
                  selected={selectedId === keyOf(card)}
                  onSelect={setSelected}
                />
              );
            })}
          </Column>

          <Column
           title="Needs You"
            count={needsYou.length}
            state={worstState(data.inbox.state, data.active.state)}
            error={data.inbox.error ?? data.active.error}
            emptyText="You are all caught up."
          >
            {needsYou.map((c) => {
              const card: BoardCard = { column: 'needs-you', card: c };
              return (
                <Card
                  key={c.id}
                  card={card}
                  selected={selectedId === keyOf(card)}
                  onSelect={setSelected}
                />
              );
            })}
          </Column>

          <Column
            title="Done"
            count={done.length}
            state={worstState(data.withDraft.state, data.episodes.state)}
            error={data.withDraft.error ?? data.episodes.error}
            emptyText="No finished projects yet."
          >
            {done.map((c) => {
              const card: BoardCard = { column: 'done', card: c };
              return (
                <Card
                  key={c.project.id}
                  card={card}
                  selected={selectedId === keyOf(card)}
                  onSelect={setSelected}
                />
              );
            })}
          </Column>
        </div>
      )}

      {selected && (
        <InspectDrawer
          card={selected}
          onClose={() => setSelected(null)}
          onResolved={() => {
            // Optimistically drop the resolved approval, refetch the inbox,
            // and close the drawer — the SSE audit.approval.* will reconcile.
            if (selected.column === 'needs-you' && selected.card.kind === 'approval') {
              data.dropInbox(selected.card.id);
            }
            void data.refetchInbox();
            setSelected(null);
          }}
          onCommented={() => void data.refetchInbox()}
        />
      )}
    </section>
  );
}

/** Stable identity for a card so selection survives live refetches. */
function keyOf(card: BoardCard): string {
  switch (card.column) {
    case 'backlog':
      return `backlog:${card.project.id}`;
    case 'inflight':
      return `inflight:${card.card.project.id}`;
    case 'needs-you':
      return `needs-you:${card.card.id}`;
    case 'done':
      return `done:${card.card.project.id}`;
  }
}

function selectedKey(card: BoardCard | null): string | null {
  return card ? keyOf(card) : null;
}
