import { describe, expect, it } from 'vitest';
import { triggerForEvent } from '../src/api/events';

describe('triggerForEvent (SSE type → refetch trigger)', () => {
  it('routes approval audit events to inbox', () => {
    expect(triggerForEvent('audit.approval.approved')).toBe('inbox');
    expect(triggerForEvent('audit.approval.rejected')).toBe('inbox');
    expect(triggerForEvent('audit.approval.revision_requested')).toBe('inbox');
    expect(triggerForEvent('audit.approval.snoozed')).toBe('inbox');
    expect(triggerForEvent('audit.approval.cancelled')).toBe('inbox');
  });

  it('routes project audit + harness events to projects', () => {
    expect(triggerForEvent('audit.project.activated')).toBe('projects');
    expect(triggerForEvent('audit.project.kickoff_scheduled')).toBe('projects');
    expect(triggerForEvent('harness.event')).toBe('projects');
  });

  it('routes episode events to episodes', () => {
    expect(triggerForEvent('episode.recorded')).toBe('episodes');
    expect(triggerForEvent('audit.learning.episode_recorded')).toBe('episodes');
  });

  it('routes llm.spend to the spend metric', () => {
    expect(triggerForEvent('llm.spend')).toBe('spend');
  });

  it('routes runtime audit events to the runtime strip', () => {
    expect(triggerForEvent('audit.runtime.suspended')).toBe('runtime');
    expect(triggerForEvent('audit.runtime.resumed')).toBe('runtime');
  });

  it('ignores unrelated events', () => {
    expect(triggerForEvent('daemon.tick')).toBeNull();
    expect(triggerForEvent('self_update.event')).toBeNull();
    expect(triggerForEvent('audit.observability.snapshot')).toBeNull();
    expect(triggerForEvent('audit.health.degraded')).toBeNull();
    expect(triggerForEvent('something.random')).toBeNull();
  });
});
