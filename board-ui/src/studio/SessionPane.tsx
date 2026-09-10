import { useEffect, useRef, useState } from 'react';
import { KIND_GLYPH, totalSpend, type Line } from './activity';

interface Props {
  role: string | null;
  lines: Line[];
  task?: string | null;
}

function hhmmss(ts: string): string {
  const m = /T(\d{2}:\d{2}:\d{2})/.exec(ts);
  if (m?.[1]) return m[1];
  return ts.length >= 19 ? ts.slice(11, 19) : ts;
}

export function SessionPane({ role, lines, task }: Props) {
  const scroller = useRef<HTMLDivElement | null>(null);
  const [follow, setFollow] = useState(true);
  const last = lines.length ? lines[lines.length - 1] : null;
  const live = last?.source === 'live';

  useEffect(() => {
    if (follow && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [lines.length, follow]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (atBottom !== follow) setFollow(atBottom);
  };

  const spend = totalSpend(lines);
  return (
    <section className="studio__col studio__session" aria-label="Session">
      <header className="studio__head">
        <span>session · <b>{role ?? 'company'}</b>{task ? <b> · {task}</b> : null}</span>
        <span>{spend > 0 ? `$${spend.toFixed(3)}` : ''}</span>
      </header>
      <div className="studio__scroll" ref={scroller} onScroll={onScroll}>
        {lines.length === 0 ? (
          <div className="sess__empty">
            {role ? `${role} has no recent activity. Lines appear here the moment it works.` : 'Nothing is happening yet. Give the CEO an intent below.'}
          </div>
        ) : (
          <div className="sess">
            {lines.map((l, i) => (
              <div className="sess__line" key={`${l.ts}-${i}`}>
                <span className="sess__ts">{hhmmss(l.ts)}</span>
                <span className={`sess__k sess__k--${l.kind}`} title={l.kind}>{KIND_GLYPH[l.kind]}</span>
                <span className="sess__v">
                  {l.text}
                  {l.detail ? <span className="sess__d">— {l.detail}</span> : null}
                  {i === lines.length - 1 && live ? <span className="sess__cursor" /> : null}
                </span>
              </div>
            ))}
          </div>
        )}
        {!follow && lines.length > 0 && (
          <button type="button" className="sess__follow" onClick={() => setFollow(true)}>↓ follow live</button>
        )}
      </div>
    </section>
  );
}
