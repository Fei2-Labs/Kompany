// Pure request-body builders for the `/approvals/{id}/*` actions. Kept here
// (no fetch, no React) so the EXACT payload shapes are unit-testable in
// isolation against the backend Pydantic request models.
//
// Field names verified against interfaces/api_parts/models.py (2026-06-15):
//   ApproveApprovalRequest  { comment: str = "" }
//   RejectApprovalRequest   { reason: str = "", comment: str = "" }
//   ReviseApprovalRequest   { counter: str (required), comment: str = "" }
//   SnoozeApprovalRequest   { minutes: int (required), comment: str = "" }
//   CancelApprovalRequest   { reason: str = "", comment: str = "" }
//   CommentApprovalRequest  { body: str (required), by_type: str = "user", by_id: str|None }
//
// Builders omit empty optional fields so the backend defaults apply, and they
// trim free-text so an all-whitespace comment is treated as absent.

export type ApprovalActionKind =
  | 'approve'
  | 'reject'
  | 'revise'
  | 'snooze'
  | 'cancel'
  | 'comment';

export interface ApproveInput {
  comment?: string;
}
export interface RejectInput {
  reason: string;
  comment?: string;
}
export interface ReviseInput {
  counter: string;
  comment?: string;
}
export interface SnoozeInput {
  minutes: number;
  comment?: string;
}
export interface CancelInput {
  reason?: string;
  comment?: string;
}
export interface CommentInput {
  body: string;
  by_type?: string;
  by_id?: string;
}

function clean(s: string | undefined): string | undefined {
  if (s == null) return undefined;
  const t = s.trim();
  return t.length > 0 ? t : undefined;
}

/** `POST /approvals/{id}/approve` body — `{ comment? }`. */
export function buildApprovePayload(input: ApproveInput = {}): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  const comment = clean(input.comment);
  if (comment) body.comment = comment;
  return body;
}

/** `POST /approvals/{id}/reject` body — `{ reason, comment? }`. */
export function buildRejectPayload(input: RejectInput): Record<string, unknown> {
  const body: Record<string, unknown> = { reason: input.reason.trim() };
  const comment = clean(input.comment);
  if (comment) body.comment = comment;
  return body;
}

/** `POST /approvals/{id}/revise` body — `{ counter, comment? }`. `counter` required. */
export function buildRevisePayload(input: ReviseInput): Record<string, unknown> {
  const body: Record<string, unknown> = { counter: input.counter.trim() };
  const comment = clean(input.comment);
  if (comment) body.comment = comment;
  return body;
}

/** `POST /approvals/{id}/snooze` body — `{ minutes, comment? }`. `minutes` required. */
export function buildSnoozePayload(input: SnoozeInput): Record<string, unknown> {
  const body: Record<string, unknown> = { minutes: Math.trunc(input.minutes) };
  const comment = clean(input.comment);
  if (comment) body.comment = comment;
  return body;
}

/** `POST /approvals/{id}/cancel` body — `{ reason?, comment? }`. */
export function buildCancelPayload(input: CancelInput = {}): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  const reason = clean(input.reason);
  const comment = clean(input.comment);
  if (reason) body.reason = reason;
  if (comment) body.comment = comment;
  return body;
}

/** `POST /approvals/{id}/comment` body — `{ body, by_type, by_id? }`. */
export function buildCommentPayload(input: CommentInput): Record<string, unknown> {
  const body: Record<string, unknown> = {
    body: input.body.trim(),
    by_type: input.by_type ?? 'user',
  };
  const byId = clean(input.by_id);
  if (byId) body.by_id = byId;
  return body;
}
