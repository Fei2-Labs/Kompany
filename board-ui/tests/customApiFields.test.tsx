import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { CustomAPIFields } from '../src/panes/settings/CustomAPIFields';

vi.mock('../src/panes/useAsync', () => ({
  useAsync: () => ({
    state: 'ready',
    data: { base_url: 'https://provider.example/v1', api_key_configured: true },
    reload: vi.fn(),
  }),
}));

describe('custom API settings', () => {
  it('renders labeled fields without recovering the configured key', () => {
    const html = renderToStaticMarkup(createElement(CustomAPIFields, { onSaved: vi.fn() }));
    expect(html).toContain('Base URL');
    expect(html).toContain('API key');
    expect(html).toContain('type="password"');
    expect(html).toContain('Configured — leave blank to keep');
    expect(html).toContain('Save API connection');
    expect(html).toContain('Enter it again when changing the URL');
  });
});
