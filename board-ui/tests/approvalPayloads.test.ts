import { describe, expect, it } from 'vitest';
import {
  buildApprovePayload,
  buildCancelPayload,
  buildCommentPayload,
  buildRejectPayload,
  buildRevisePayload,
  buildSnoozePayload,
} from '../src/api/approvalPayloads';

// Field names verified against interfaces/api_parts/models.py (2026-06-15):
//   approve { comment? }  reject { reason, comment? }  revise { counter, comment? }
//   snooze { minutes, comment? }  cancel { reason?, comment? }
//   comment { body, by_type, by_id? }

describe('buildApprovePayload', () => {
  it('omits an empty comment so the backend default ("") applies', () => {
    expect(buildApprovePayload()).toEqual({});
    expect(buildApprovePayload({ comment: '   ' })).toEqual({});
  });
  it('includes a trimmed comment when present', () => {
    expect(buildApprovePayload({ comment: '  looks good  ' })).toEqual({
      comment: 'looks good',
    });
  });
});

describe('buildRejectPayload', () => {
  it('always sends reason (required field, trimmed)', () => {
    expect(buildRejectPayload({ reason: '  too risky ' })).toEqual({
      reason: 'too risky',
    });
  });
  it('adds comment only when non-empty', () => {
    expect(buildRejectPayload({ reason: 'no', comment: 'see notes' })).toEqual({
      reason: 'no',
      comment: 'see notes',
    });
    expect(buildRejectPayload({ reason: 'no', comment: '  ' })).toEqual({
      reason: 'no',
    });
  });
});

describe('buildRevisePayload', () => {
  it('sends the required counter field (trimmed)', () => {
    expect(buildRevisePayload({ counter: '  half the budget ' })).toEqual({
      counter: 'half the budget',
    });
  });
  it('adds an optional comment', () => {
    expect(buildRevisePayload({ counter: 'x', comment: 'why' })).toEqual({
      counter: 'x',
      comment: 'why',
    });
  });
});

describe('buildSnoozePayload', () => {
  it('sends minutes as a truncated integer', () => {
    expect(buildSnoozePayload({ minutes: 90.7 })).toEqual({ minutes: 90 });
  });
  it('adds an optional comment', () => {
    expect(buildSnoozePayload({ minutes: 30, comment: 'later' })).toEqual({
      minutes: 30,
      comment: 'later',
    });
  });
});

describe('buildCancelPayload', () => {
  it('omits both optional fields when empty', () => {
    expect(buildCancelPayload()).toEqual({});
    expect(buildCancelPayload({ reason: ' ', comment: '' })).toEqual({});
  });
  it('includes reason and comment when present', () => {
    expect(buildCancelPayload({ reason: 'obsolete', comment: 'moved on' })).toEqual({
      reason: 'obsolete',
      comment: 'moved on',
    });
  });
});

describe('buildCommentPayload', () => {
  it('sends body + defaults by_type to "user"', () => {
    expect(buildCommentPayload({ body: '  noted ' })).toEqual({
      body: 'noted',
      by_type: 'user',
    });
  });
  it('respects an explicit by_type and includes by_id when present', () => {
    expect(
      buildCommentPayload({ body: 'hi', by_type: 'agent', by_id: 'ceo' }),
    ).toEqual({ body: 'hi', by_type: 'agent', by_id: 'ceo' });
  });
  it('omits an empty by_id', () => {
    expect(buildCommentPayload({ body: 'hi', by_id: '  ' })).toEqual({
      body: 'hi',
      by_type: 'user',
    });
  });
});
