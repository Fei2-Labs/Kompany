import { NavLink } from 'react-router-dom';

// The "Live" pane embeds the old cyberpunk terminal (served by FastAPI at
// `/ui/`) in an iframe *inside* the board shell. This is deliberate: a plain
// link navigates the whole Tauri WebView to `/ui/`, which has no link back and
// no browser chrome, stranding the founder. Embedding keeps the nav rail, so
// any nav click — or the explicit "Back to board" button — returns instantly.
//
// Same-origin iframe (both served by the one FastAPI app), so the terminal's
// relative fetch/SSE calls work unchanged. No sandbox attribute — it needs
// network + EventSource.
export function Live() {
  return (
    <section className="pane pane--live">
      <header className="live__head">
        <h1 className="pane__title">Live · Terminal</h1>
        <NavLink to="/" className="btn btn--ghost btn--sm">
          ← Back to board
        </NavLink>
      </header>
      <iframe className="live__frame" src="/ui/" title="Kompany live terminal" />
    </section>
  );
}
