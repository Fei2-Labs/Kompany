// Inspect drawer for a selected card. For Needs-You approval cards it mounts
// the live ApprovalActions footer (approve / reject / revise / snooze / comment /
// cancel) with inline forms — no native dialogs (Tauri WebView). Other columns
// render read-only detail.

import type { BoardCard } from './Card';
import { money, percent, target } from './format';
import { ApprovalActions } from './ApprovalActions';

/** Action intents the drawer dispatches. */
export type CardAction =
  | 'approve'
  | 'reject'
  | 'revise'
  | 'snooze'
  | 'comment'
  | 'inspect-project';

interface InspectDrawerProps {
  card: BoardCard;
  onClose: () => void;
  /** A resolving approval action landed — drop the row + close the drawer. */
  onResolved?: () => void;
  /** A comment landed — refetch /inbox but keep the drawer open. */
  onCommented?: () => void;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="drawer__row">
      <span className="drawer__row-label">{label}</span>
      <span className="drawer__row-value">{value}</span>
    </div>
  );
}

export function InspectDrawer({
  card,
  onClose,
  onResolved,
  onCommented,
}: InspectDrawerProps) {
  // The action footer is live only for Needs-You approval cards; blocked tasks
  // are a separate "connect account" surface (no approval row to act on).
  const approval =
    card.column === 'needs-you' && card.card.kind === 'approval'
      ? card.card.approval
      : undefined;

  return (
    <aside className="drawer">
      <header className="drawer__header">
        <span className="drawer__eyebrow">{drawerEyebrow(card)}</span>
        <button
          type="button"
          className="drawer__close"
          onClick={onClose}
          aria-label="Close"
        >
          ✕
        </button>
      </header>
      <div className="drawer__body">{drawerBody(card)}</div>
      <footer className="drawer__actions">
        {approval ? (
          <ApprovalActions
            approval={approval}
            onResolved={() => onResolved?.()}
            onCommented={() => onCommented?.()}
          />
        ) : card.column === 'needs-you' ? (
          <span className="drawer__actions-hint">
            Blocked task — connect the account it needs from the agent&apos;s
            request. No approval to act on here.
          </span>
        ) : (
          <span className="drawer__actions-hint">
            No founder action required on this card.
          </span>
        )}
      </footer>
    </aside>
  );
}

function drawerEyebrow(card: BoardCard): string {
  switch (card.column) {
    case 'backlog':
      return 'Backlog · draft project';
    case 'inflight':
      return 'In-Flight · active project';
    case 'needs-you':
      return card.card.kind === 'approval'
        ? 'Needs You · approval'
        : 'Needs You · blocked task';
    case 'done':
      return card.card.cancelled ? 'Done · cancelled' : 'Done · completed';
  }
}

function drawerBody(card: BoardCard) {
  switch (card.column) {
    case 'backlog':
      return (
        <>
          <h2 className="drawer__title">{card.project.name}</h2>
          <Row label="Type" value={card.project.type} />
          <Row label="Target" value={target(card.project.target_amount)} />
        </>
      );
    case 'inflight': {
      const { project, detail, progress, taskCounts } = card.card;
      return (
        <>
          <h2 className="drawer__title">{project.name}</h2>
          <Row label="Type" value={project.type} />
          <Row
            label="Funding"
            value={`${money(project.funded_amount)} / ${target(project.target_amount)} (${percent(progress)})`}
          />
          {taskCounts && (
            <Row
              label="Tasks"
              value={`${taskCounts.done}/${taskCounts.total} done`}
            />
          )}
          {detail && detail.assigned_agents.length > 0 && (
            <Row label="Agents" value={detail.assigned_agents.join(', ')} />
          )}
          {detail && detail.tasks.length > 0 && (
            <ul className="drawer__tasks">
              {detail.tasks.map((t) => (
                <li key={t.id} className="drawer__task">
                  <span className={`dot dot--${t.status}`} />
                  <span className="drawer__task-title">{t.title}</span>
                  <span className="drawer__task-agent">{t.agent}</span>
                  <span className="drawer__task-status">{t.status}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      );
    }
    case 'needs-you': {
      const c = card.card;
      return (
        <>
          <h2 className="drawer__title">{c.title}</h2>
          <Row label="Badge" value={c.badge} />
          <Row label="Severity" value={c.severity} />
          {c.kind === 'approval' && c.approval && (
            <>
              <Row label="Action type" value={c.approval.action_type} />
              <Row label="Status" value={c.approval.status} />
              {c.approval.requested_by && (
                <Row label="Requested by" value={c.approval.requested_by} />
              )}
            </>
          )}
          {c.kind === 'task' && (
            <>
              <Row label="Task status" value={c.taskStatus ?? ''} />
              {c.projectName && <Row label="Project" value={c.projectName} />}
              {c.agent && <Row label="Agent" value={c.agent} />}
            </>
          )}
        </>
      );
    }
    case 'done': {
      const c = card.card;
      return (
        <>
          <h2 className="drawer__title">{c.project.name}</h2>
          <Row label="Type" value={c.project.type} />
          <Row label="Outcome" value={c.cancelled ? 'cancelled' : 'completed'} />
          {c.summary && (
            <p className="drawer__summary">{c.summary}</p>
          )}
        </>
      );
    }
  }
}
