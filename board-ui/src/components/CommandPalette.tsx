import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { NAV_GROUPS, TOP_ITEMS } from '../nav';

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  /** Focus the CEO command input (the "send to CEO" action). */
  onSendToCeo: () => void;
}

interface Command {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

/** Tiny subsequence fuzzy match: every query char appears in order. */
function fuzzyMatch(query: string, target: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  let i = 0;
  for (let j = 0; j < t.length && i < q.length; j += 1) {
    if (t[j] === q[i]) i += 1;
  }
  return i === q.length;
}

/**
 * ⌘K command palette: fuzzy-jump to any nav pane + a "send to CEO" action that
 * focuses the command input. Inline UI — no native dialogs (Tauri WebView).
 */
export function CommandPalette({ open, onClose, onSendToCeo }: CommandPaletteProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);

  const commands = useMemo<Command[]>(() => {
    const navItems = [...TOP_ITEMS, ...NAV_GROUPS.flatMap((g) => g.items)];
    const jumps: Command[] = navItems
      .filter((it) => it.path)
      .map((it) => ({
        id: `nav:${it.path}`,
        label: `Go to ${it.label}`,
        hint: it.path ?? undefined,
        run: () => {
          navigate(it.path as string);
          onClose();
        },
      }));
    const send: Command = {
      id: 'ceo:send',
      label: 'Send to CEO',
      hint: 'kompany>',
      run: () => {
        navigate('/talk');
        onSendToCeo();
        onClose();
      },
    };
    return [send, ...jumps];
  }, [navigate, onClose, onSendToCeo]);

  const filtered = useMemo(
    () => commands.filter((c) => fuzzyMatch(query, c.label)),
    [commands, query],
  );

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setActive(0);
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Keep the active index in range as the filter narrows.
  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  if (!open) return null;

  const onInputKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      filtered[active]?.run();
    }
  };

  return (
    <div className="palette__backdrop" onClick={onClose} role="presentation">
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="palette__input"
          placeholder="Jump to a pane, or run a command…"
          aria-label="Command input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onInputKey}
        />
        {filtered.length === 0 ? (
          <div className="palette__empty">No matching commands.</div>
        ) : (
          <ul className="palette__list">
            {filtered.map((c, i) => (
              <li key={c.id}>
                <button
                  type="button"
                  className={i === active ? 'palette__item palette__item--active' : 'palette__item'}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => c.run()}
                >
                  <span className="palette__item-label">{c.label}</span>
                  {c.hint && <span className="palette__item-hint">{c.hint}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
