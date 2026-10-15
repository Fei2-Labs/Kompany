import { describe, expect, it } from 'vitest';
import { newer, pendingUpdates } from '../src/panes/settings/updateVersions';

describe('update versions', () => {
  it('compares numerically and ignores build suffixes', () => {
    expect(newer('0.1.10', '0.1.9')).toBe(true);
    expect(newer('0.1.18', '0.1.18')).toBe(false);
    expect(newer('0.1.18', '0.1.18+pro0.1.7')).toBe(false);
    expect(newer(undefined, '0.1.18')).toBe(false);
  });

  it('names a Pro-only update instead of the unchanged core version', () => {
    expect(pendingUpdates('0.1.18', '0.1.18', '0.1.5', '0.1.7')).toBe('pro 0.1.7');
  });

  it('lists both packages when both are behind', () => {
    expect(pendingUpdates('0.1.17', '0.1.18', '0.1.5', '0.1.7')).toBe('kompany 0.1.18 · pro 0.1.7');
  });

  it('ignores the Pro feed when Pro is not installed', () => {
    expect(pendingUpdates('0.1.18', '0.1.18', null, '0.1.7')).toBe('');
  });
});
