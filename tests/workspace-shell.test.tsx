// @vitest-environment jsdom

import './setup';

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import StageNav, { WorkspaceShell, withContext } from '../components/workspace-shell';

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

  it('renders the 3-zone SaaS workbench with unpinned tenant context, breadcrumb subbar, and ] inspector toggle', () => {
    window.history.pushState({}, '', '/proposals');
    const { container } = render(
      <WorkspaceShell
        user={{ displayName: 'Ada Lovelace', email: 'ada@sites.test' }}
        inspectorTitle="Proposal inspector"
        inspector={<p>Scoped inspector guarantees</p>}
      >
        <div>Stage canvas body</div>
      </WorkspaceShell>,
    );

    const shell = container.querySelector('main.wb-shell');
    expect(shell).toBeTruthy();

    const leftSpine = screen.getByRole('complementary', { name: 'Workspace navigation' });
    expect(within(leftSpine).getByText('None pinned')).toBeTruthy();
    expect(within(leftSpine).getByText('Unfrozen draft')).toBeTruthy();
    expect(within(leftSpine).getByText('Ada Lovelace')).toBeTruthy();

    expect(screen.getByText('Workspace')).toBeTruthy();
    expect(screen.getByText('04 Proposals · Compile move')).toBeTruthy();
    expect(screen.getByText('Stage canvas body')).toBeTruthy();

    const rightInspector = screen.getByRole('complementary', { name: 'Stage inspector' });
    expect(within(rightInspector).getByText('Proposal inspector')).toBeTruthy();
    expect(within(rightInspector).getByText('Scoped inspector guarantees')).toBeTruthy();
    expect(rightInspector.classList.contains('is-collapsed')).toBe(false);
    expect(shell?.classList.contains('is-right-collapsed')).toBe(false);

    fireEvent.keyDown(window, { key: ']' });
    expect(rightInspector.classList.contains('is-collapsed')).toBe(true);
    expect(shell?.classList.contains('is-right-collapsed')).toBe(true);

    fireEvent.keyDown(window, { key: ']' });
    expect(rightInspector.classList.contains('is-collapsed')).toBe(false);
    expect(shell?.classList.contains('is-right-collapsed')).toBe(false);
  });
});
