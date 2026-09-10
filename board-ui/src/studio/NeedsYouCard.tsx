// One NEEDS YOU card. Money/decision approvals expand into the existing
// ApprovalActions footer (same endpoints + payloads as the board). Account
// cards are one click to Settings → Integrations. Blocked-task escalations
// hand the question to the CEO through the shared channel.

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApprovalActions } from '../board/ApprovalActions';
import type { UseChannel } from '../channel/useChannel';
import { KIND_LABEL, type NeedsItem } from './needsYou';

interface NeedsYouCardProps {
  item: NeedsItem;
  channel?: UseChannel;
  onResolved: (id: string) => void;
  /** Compact rail rendering (Studio right column) vs full pane. */
  compact?: boolean;
}

export function NeedsYouCard({ item, channel, onResolved, compact }: NeedsYouCardProps) {
  const [open, setOpen] = useState(!compact);
  const navigate = useNavigate();
  const [hidden, setHidden] = useState(false);
  if (hidden) return null;

  const meta = [item.agent, item.projectName, item.severity !== 'medium' ? item.severity : null]
    .filter(Boolean)
    .join(' · ');

  return (
    <article className={`ny-card ny-card--${item.kind}`} data-testid="needs-you-card">
      <header className="ny-card__head" onClick={() => compact && setOpen((o) => !o)}>
        <span className={`rail__tag rail__tag--${item.kind}`}>{KIND_LABEL[item.kind]}</span>
        <span className="ny-card__title">{item.title}</span>
      </header>
      {meta && <div className="ny-card__meta">{meta}</div>}
      {open && item.approval && (
        <>
          {!compact && Object.keys(item.approval.payload).length > 0 && (
            <pre className="ny-card__payload">{JSON.stringify(item.approval.payload, null, 2)}</pre>
          )}
          <ApprovalActions
            approval={item.approval}
            onResolved={() => {
              setHidden(true);
              onResolved(item.id);
            }}
            onCommented={() => onResolved(item.id)}
          />
        </>
      )}
      {open && item.integration && (
        <div className="ny-card__actions">
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={() => navigate(`/settings#integration-${item.integration!.integration_id}`)}
          >
            Connect {item.integration.display_name}
          </button>
        </div>
      )}
      {open && item.escalation && (
        <div className="ny-card__actions">
          <button
            type="button"
            className="btn btn--primary btn--sm"
            disabled={!channel || channel.busy}
            onClick={() => {
              if (!channel) return;
              void channel.send(item.escalation!);
              navigate('/talk');
            }}
          >
            Ask the CEO
          </button>
        </div>
      )}
    </article>
  );
}
