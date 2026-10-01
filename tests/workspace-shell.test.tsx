// @vitest-environment jsdom

import './setup';

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import StageNav, { withContext } from '../components/workspace-shell';

afterEach(() => cleanup());

describe('workspace stage navigation', () => {
  it('preserves workspace context before a guide anchor and excludes unrelated parameters', () => {
    const params = new URLSearchParams('paper_id=p-1&spec_id=s-1&candidate_id=c-1&unrelated=ignored');
    expect(withContext('/graph#paper-import', params)).toBe('/graph?paper_id=p-1&spec_id=s-1&candidate_id=c-1#paper-import');
    expect(withContext('/help', params)).toBe('/help?paper_id=p-1&spec_id=s-1&candidate_id=c-1');
  });

  it('exposes one compact navigation surface with all five research stages', () => {
    window.history.pushState({}, '', '/proposals?paper_id=p-1&spec_id=s-1');
    render(<StageNav />);

    expect(screen.getByRole('navigation', { name: 'Research stages' })).toBeTruthy();
    expect(screen.getAllByRole('link')).toHaveLength(5);
    expect(screen.getByRole('link', { name: /Proposals/i }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('link', { name: /Reports/i }).getAttribute('href')).toBe('/reports?paper_id=p-1&spec_id=s-1');
  });
});
