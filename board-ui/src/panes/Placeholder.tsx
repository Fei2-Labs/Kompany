interface PlaceholderProps {
  title: string;
  hint?: string;
}

/**
 * Empty routed pane. Real content lands in PR2-PR4; for PR1 every nav
 * target renders one of these with a heading so the shell is navigable.
 */
export function Placeholder({ title, hint }: PlaceholderProps) {
  return (
    <section className="pane">
      <header className="pane__header">
        <h1 className="pane__title">{title}</h1>
      </header>
      <div className="pane__empty">
        <p>{hint ?? 'Coming soon.'}</p>
      </div>
    </section>
  );
}
