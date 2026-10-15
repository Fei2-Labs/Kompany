import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { UpdateProgress } from '../src/panes/settings/UpdateProgress';

const render = (phase: string) => renderToStaticMarkup(createElement(UpdateProgress, { phase }));

describe('engine update progress', () => {
  it.each(['checking', 'backing_up', 'downloading', 'verifying', 'installing', 'switching', 'restarting'])(
    'shows an accessible indeterminate bar for %s', (phase) => {
      const html = render(phase);
      expect(html).toContain('<progress');
      expect(html).toContain('aria-label=');
      expect(html).toContain('role="status"');
      expect(html).not.toMatch(/<progress[^>]*value=/);
    },
  );
  it.each(['idle', 'done', 'failed', 'rolled_back', 'unknown'])(
    'hides the bar for %s', (phase) => expect(render(phase)).toBe(''),
  );
  it('does not imply a measured percentage during installation', () => {
    expect(render('installing')).toContain('does not report a download percentage');
    expect(render('installing').replace(/<[^>]*>/g, '')).not.toContain('%');
  });
});
