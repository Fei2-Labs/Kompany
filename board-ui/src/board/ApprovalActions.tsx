// Inline approval-action footer for the InspectDrawer (PR3 action seam).
// Approve / reject / revise / snooze / comment — each expanding into an inline
// form (textarea for reason/comment, number for minutes). NO native dialogs
// (Tauri WebView). On success: refetch /inbox (caller-provided) + show the
// returned `effect`/`tool_result` for HARNESS_EFFECT_ACTIONS approvals.

import { useState } from 'react';
import {
  ApiError,
  approveApproval,
  cancelApproval,
  commentApproval,
  rejectApproval,
  reviseApproval,
  snoozeApproval,
  type ApprovalResult,
} from '../api/client';
import type { ApprovalRequest } from '../api/types';

type Mode = 'idle' | 'reject' | 'revise' | 'snooze' | 'cancel' | 'comment';

interface ApprovalActionsProps {
  approval: ApprovalRequest;
  /** A resolving action landed (approve/reject/revise/snooze/cancel) — caller
   *  optimistically drops the row + closes the drawer. */
  onResolved: () => void;
  /** A non-resolving action landed (comment) — caller refetches, drawer stays. */
  onCommented?: () => void;
}

function errMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.status === 0 ? 'Engine unreachable' : `Request failed (${err.status})`;
  }
  return err instanceof Error ? err.message : 'Unknown error';
}

export function ApprovalActions({ approval, onResolved, onCommented }: ApprovalActionsProps) {
  const [mode, setMode] = useState<Mode>('idle');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ApprovalResult | null>(null);

  // Form fields (reused across modes; only the relevant ones are shown).
  const [comment, setComment] = useState('');
  const [reason, setReason] = useState('');
  const [counter, setCounter] = useState('');
  const [minutes, setMinutes] = useState(60);
  const [body, setBody] = useState('');

  const run = async (fn: () => Promise<ApprovalResult>, resolving: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      setResult(res);
      setMode('idle');
      if (resolving) {
        // Resolved/spawned — caller drops the row + closes the drawer.
        onResolved();
      } else {
        // Comment doesn't resolve — keep the drawer usable, just refetch.
        setComment('');
        setBody('');
        onCommented?.();
      }
    } catch (err) {
      setError(errMessage(err));
    } finally {
      setBusy(false);
    }
  };

  // The `effect` / `tool_result` from a HARNESS_EFFECT_ACTIONS approval.
  const effect = result?.effect;
  const toolResult = result?.tool_result;

  return (
    <div className="approve">
      {error && <div className="approve__error">{error}</div>}

      {(effect !== undefined || toolResult !== undefined) && (
        <div className="approve__effect">
          <span className="approve__effect-label">Effect</span>
          <pre className="approve__effect-body">
            {JSON.stringify(toolResult ?? effect, null, 2)}
          </pre>
        </div>
      )}

      {mode === 'idle' && (
        <div className="approve__buttons">
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy}
            onClick={() => void run(() => approveApproval(approval.id, { comment }), true)}
          >
            Approve
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => setMode('reject')}>
            Reject
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => setMode('revise')}>
            Revise
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => setMode('snooze')}>
            Snooze
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => setMode('comment')}>
            Comment
          </button>
          <button type="button" className="btn btn--danger" disabled={busy} onClick={() => setMode('cancel')}>
            Cancel
          </button>
        </div>
      )}

      {mode === 'reject' && (
        <Form
          title="Reject"
          submitLabel="Reject"
          busy={busy}
          disabled={reason.trim().length === 0}
          onCancel={() => setMode('idle')}
          onSubmit={() => void run(() => rejectApproval(approval.id, { reason, comment }), true)}
        >
          <textarea
            className="approve__field"
            placeholder="Reason (required)…"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
          />
          <textarea
            className="approve__field"
            placeholder="Comment (optional)…"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={2}
          />
        </Form>
      )}

      {mode === 'revise' && (
        <Form
          title="Counter-propose"
          submitLabel="Request revision"
          busy={busy}
          disabled={counter.trim().length === 0}
          onCancel={() => setMode('idle')}
          onSubmit={() => void run(() => reviseApproval(approval.id, { counter, comment }), true)}
        >
          <textarea
            className="approve__field"
            placeholder="Counter-proposal (required)…"
            value={counter}
            onChange={(e) => setCounter(e.target.value)}
            rows={3}
          />
          <textarea
            className="approve__field"
            placeholder="Comment (optional)…"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={2}
          />
        </Form>
      )}

      {mode === 'snooze' && (
        <Form
          title="Snooze"
          submitLabel="Snooze"
          busy={busy}
          disabled={!Number.isFinite(minutes) || minutes <= 0}
          onCancel={() => setMode('idle')}
          onSubmit={() => void run(() => snoozeApproval(approval.id, { minutes, comment }), true)}
        >
          <label className="approve__inline">
            Minutes
            <input
              className="approve__number"
              type="number"
              min={1}
              value={minutes}
              onChange={(e) => setMinutes(Number(e.target.value))}
            />
          </label>
          <textarea
            className="approve__field"
            placeholder="Comment (optional)…"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={2}
          />
        </Form>
      )}

      {mode === 'cancel' && (
        <Form
          title="Cancel approval"
          submitLabel="Cancel approval"
          busy={busy}
          disabled={false}
          onCancel={() => setMode('idle')}
          onSubmit={() => void run(() => cancelApproval(approval.id, { reason, comment }), true)}
        >
          <textarea
            className="approve__field"
            placeholder="Reason (optional)…"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
          />
        </Form>
      )}

      {mode === 'comment' && (
        <Form
          title="Comment"
          submitLabel="Add comment"
          busy={busy}
          disabled={body.trim().length === 0}
          onCancel={() => setMode('idle')}
          onSubmit={() => void run(() => commentApproval(approval.id, { body }), false)}
        >
          <textarea
            className="approve__field"
            placeholder="Comment…"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={3}
          />
        </Form>
      )}
    </div>
  );
}

interface FormProps {
  title: string;
  submitLabel: string;
  busy: boolean;
  disabled: boolean;
  onCancel: () => void;
  onSubmit: () => void;
  children: React.ReactNode;
}

function Form({ title, submitLabel, busy, disabled, onCancel, onSubmit, children }: FormProps) {
  return (
    <div className="approve__form">
      <span className="approve__form-title">{title}</span>
      {children}
      <div className="approve__form-actions">
        <button type="button" className="btn" disabled={busy} onClick={onCancel}>
          Back
        </button>
        <button
          type="button"
          className="btn btn--primary"
          disabled={busy || disabled}
          onClick={onSubmit}
        >
          {busy ? 'Working…' : submitLabel}
        </button>
      </div>
    </div>
  );
}
