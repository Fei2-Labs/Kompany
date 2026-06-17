// A board column: header with count, per-column load/empty/error states
// (inline — no native dialogs), and the list of cards.

import type { ReactNode } from 'react';
import type { LoadState } from './useBoardData';

interface ColumnProps {
  title: string;
  count: number;
  state: LoadState;
  error: string | null;
  emptyText: string;
  children: ReactNode;
}

export function Column({
  title,
  count,
  state,
  error,
  emptyText,
  children,
}: ColumnProps) {
  return (
    <section className="column">
      <header className="column__header">
        <span className="column__title">{title}</span>
        <span className="column__count">{count}</span>
      </header>
      <div className="column__body">
        {state === 'loading' && (
          <div className="column__status">Loading…</div>
        )}
        {state === 'error' && (
          <div className="column__status column__status--error">
            {error ?? 'Failed to load.'}
          </div>
        )}
        {state === 'ready' && count === 0 && (
          <div className="column__status column__status--empty">{emptyText}</div>
        )}
        {state === 'ready' && count > 0 && children}
      </div>
    </section>
  );
}
