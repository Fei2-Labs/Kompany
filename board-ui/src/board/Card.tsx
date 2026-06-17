// A single board card. Read-only here (PR2): click selects the card and the
// Board surfaces an inspect drawer. The action seam for PR3 is the `onSelect`
// callback + the typed `BoardCard` union the drawer reads — no action buttons
// land until PR3.

import type { ActionBadge, NeedsYouCard } from './classify';
import type {
  DoneCard,
  InFlightCard,
} from './classify';
import type { ProjectListItem } from '../api/types';
import { percent, target } from './format';

/** Discriminated union of everything a column can render + inspect. */
export type BoardCard =
  | { column: 'backlog'; project: ProjectListItem }
  | { column: 'inflight'; card: InFlightCard }
  | { column: 'needs-you'; card: NeedsYouCard }
  | { column: 'done'; card: DoneCard };

interface CardShellProps {
  title: string;
  type?: string;
  agents?: string[];
  badge?: { label: string; tone: BadgeTone };
  progress?: number | null;
  meta?: string;
  selected: boolean;
  onSelect: () => void;
}

type BadgeTone = 'money' | 'ship-gate' | 'decision' | 'done' | 'cancelled' | 'neutral';

function badgeForAction(badge: ActionBadge): { label: string; tone: BadgeTone } {
  if (badge === 'money') return { label: 'money', tone: 'money' };
  if (badge === 'ship-gate') return { label: 'ship gate', tone: 'ship-gate' };
  return { label: 'decision', tone: 'decision' };
}

function CardShell({
  title,
  type,
  agents,
  badge,
  progress,
  meta,
  selected,
  onSelect,
}: CardShellProps) {
  return (
    <button
      type="button"
      className={selected ? 'card card--selected' : 'card'}
      onClick={onSelect}
    >
      <div className="card__top">
        <span className="card__title">{title}</span>
        {badge && (
          <span className={`badge badge--${badge.tone}`}>{badge.label}</span>
        )}
      </div>
      {(type || (agents && agents.length > 0)) && (
        <div className="card__sub">
          {type && <span className="card__type">{type}</span>}
          {agents && agents.length > 0 && (
            <span className="card__agents">{agents.join(', ')}</span>
          )}
        </div>
      )}
      {typeof progress !== 'undefined' && (
        <div className="card__progress">
          <div className="card__progress-track">
            <div
              className="card__progress-fill"
              style={{ width: `${Math.round((progress ?? 0) * 100)}%` }}
            />
          </div>
          <span className="card__progress-label">{percent(progress)}</span>
        </div>
      )}
      {meta && <div className="card__meta">{meta}</div>}
    </button>
  );
}

interface CardProps {
  card: BoardCard;
  selected: boolean;
  onSelect: (card: BoardCard) => void;
}

export function Card({ card, selected, onSelect }: CardProps) {
  const select = () => onSelect(card);

  switch (card.column) {
    case 'backlog':
      return (
        <CardShell
          title={card.project.name}
          type={card.project.type}
          badge={{ label: 'draft', tone: 'neutral' }}
          meta={`Target ${target(card.project.target_amount)}`}
          selected={selected}
          onSelect={select}
        />
      );
    case 'inflight': {
      const { project, detail, progress, taskCounts } = card.card;
      return (
        <CardShell
          title={project.name}
          type={project.type}
          agents={detail?.assigned_agents}
          progress={progress}
          meta={
            taskCounts
              ? `${taskCounts.done}/${taskCounts.total} tasks`
              : undefined
          }
          selected={selected}
          onSelect={select}
        />
      );
    }
    case 'needs-you': {
      const c = card.card;
      return (
        <CardShell
          title={c.title}
          type={c.kind === 'task' ? `blocked · ${c.projectName ?? ''}` : undefined}
          agents={c.agent ? [c.agent] : undefined}
          badge={badgeForAction(c.badge)}
          meta={`severity: ${c.severity}`}
          selected={selected}
          onSelect={select}
        />
      );
    }
    case 'done': {
      const c = card.card;
      return (
        <CardShell
          title={c.project.name}
          type={c.project.type}
          badge={
            c.cancelled
              ? { label: 'cancelled', tone: 'cancelled' }
              : { label: 'completed', tone: 'done' }
          }
          meta={c.summary ?? undefined}
          selected={selected}
          onSelect={select}
        />
      );
    }
  }
}
