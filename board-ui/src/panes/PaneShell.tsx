// Shared chrome for the read-only panes: a titled section with consistent
// loading / error / empty states so every pane behaves the same.

import type { ReactNode } from 'react';
import type { AsyncState } from './useAsync';

interface PaneShellProps {
  title: string;
  subtitle?: string;
  state: AsyncState;
  error: string | null;
  /** True when the load succeeded but produced nothing to show. */
  empty?: boolean;
  emptyText?: string;
  children: ReactNode;
}

export function PaneShell({
  title,
  subtitle,
  state,
  error,
  empty,
  emptyText,
  children,
}: PaneShellProps) {
  return (
    <section className="pane">
      <header className="pane__header">
        <h1 className="pane__title">{title}</h1>
        {subtitle && <p className="pane__subtitle">{subtitle}</p>}
      </header>
      {state === 'loading' ? (
        <div className="pane__empty">Loading…</div>
      ) : state === 'error' ? (
        <div className="pane__empty pane__empty--error">
          {error ?? 'Could not load.'}
        </div>
      ) : empty ? (
        <div className="pane__empty">{emptyText ?? 'Nothing to show yet.'}</div>
      ) : (
        children
      )}
    </section>
  );
}
