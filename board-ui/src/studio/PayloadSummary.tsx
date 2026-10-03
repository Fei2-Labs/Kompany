// Readable body of a NEEDS YOU decision card. Sections come from the pure
// summarizeApprovalPayload(); the raw JSON stays behind a collapsed
// <details> for anyone who needs the exact payload.

import type { ApprovalRequest } from '../api/types';
import { previewOf, summarizeApprovalPayload } from './approvalSummary';

/** A value too long to show in full: preview first, full text on demand. */
function LongText({ value }: { value: string }) {
  return (
    <details className="ny-summary__more">
      <summary>{previewOf(value)}</summary>
      <p className="ny-summary__text">{value}</p>
    </details>
  );
}

export function ApprovalSummary({ approval }: { approval: ApprovalRequest }) {
  const payload = approval.payload ?? {};
  if (Object.keys(payload).length === 0) return null;
  const sections = summarizeApprovalPayload(approval.action_type, payload);

  return (
    <div className="ny-summary" data-testid="approval-summary">
      {sections.map((s, i) => (
        <section className="ny-summary__section" key={i}>
          {s.title && <h4 className="ny-summary__title">{s.title}</h4>}
          {s.text && (s.long ? <LongText value={s.text} /> : <p className="ny-summary__text">{s.text}</p>)}
          {s.rows && s.rows.length > 0 && (
            <dl className="ny-summary__rows">
              {s.rows.map((r, j) => (
                <div className="ny-summary__row" key={j} style={{ paddingLeft: `${r.depth * 12}px` }}>
                  <dt>{r.label}</dt>
                  <dd>{r.long ? <LongText value={r.value} /> : r.value}</dd>
                </div>
              ))}
            </dl>
          )}
          {s.items && s.items.length > 0 && (
            <ul className="ny-summary__items">
              {s.items.map((it, j) => (
                <li key={j}>
                  <span className="ny-summary__item-text">{it.text}</span>
                  {it.badge && (
                    <span className={`ny-summary__badge ny-summary__badge--${it.tone ?? 'muted'}`}>{it.badge}</span>
                  )}
                  {it.details && (
                    <ul className="ny-summary__details">
                      {it.details.map((d, k) => (
                        <li key={k}>{d}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
      <details className="ny-summary__raw">
        <summary>Raw payload</summary>
        <pre className="ny-card__payload">{JSON.stringify(payload, null, 2)}</pre>
      </details>
    </div>
  );
}
