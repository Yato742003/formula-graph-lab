// @vitest-environment jsdom

import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ResearchWorkspace from '../app/research-workspace';
import type { WorkspaceImportResponse } from '../lib/import-types';

const completedImport: WorkspaceImportResponse = {
  job_id: 'job_12345678',
  paper: {
    paper_id: '2402.08954',
    version: 1,
    title: 'Formula Graph Research',
    authors: ['Ada Researcher', 'Grace Scientist'],
    version_published_at: '2024-02-14',
    metadata_warnings: [],
    source_url: 'https://arxiv.org/html/2402.08954v1',
    source_sha256: 'a'.repeat(64),
    sections: [
      {
        section_id: 'S1',
        anchor: 'S1',
        anchor_is_source: true,
        title: '1 Method',
        order: 1,
        parent_section_id: null,
        text: 'Method context.',
        equation_ids: ['S1.E1', 'S1.E2'],
      },
    ],
    equations: [
      {
        equation_id: 'S1.E1',
        anchor: 'S1.E1',
        anchor_is_source: true,
        latex: 'x=1',
        source_fragments: ['x=1'],
        warnings: [],
        equation_number: '1',
        section: '1 Method',
        section_id: 'S1',
        preceding_text: null,
        following_text: null,
        extraction_method: 'alttext',
        confidence: 0.98,
      },
      {
        equation_id: 'S1.E2',
        anchor: 'S1.E2',
        anchor_is_source: true,
        latex: 'y=2',
        source_fragments: ['y=2'],
        warnings: ['Review mixed content.'],
        equation_number: '2',
        section: '1 Method',
        section_id: 'S1',
        preceding_text: null,
        following_text: null,
        extraction_method: 'tex_annotation',
        confidence: 0.91,
      },
    ],
  },
  receipt: {
    import_uuid: 'import-1',
    node_count: 6,
    edge_count: 5,
    episode_count: 2,
    replayed: false,
  },
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ResearchWorkspace import interaction', () => {
  it('labels the initial graph as a curated seven-equation demo', () => {
    render(
      <ResearchWorkspace
        user={{ displayName: 'Researcher', email: 'researcher@example.com' }}
      />,
    );

    expect(screen.getByText('7 equations')).toBeTruthy();
    expect(screen.getByText(/Curated demo/)).toBeTruthy();
  });

  it('submits the canonical URL and replaces demo evidence with persisted formulas', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => completedImport,
    })) as unknown as typeof fetch;
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(
      <ResearchWorkspace
        user={{ displayName: 'Researcher', email: 'researcher@example.com' }}
      />,
    );

    const input = screen.getByRole('textbox', { name: /arXiv HTML paper URL/i });
    await user.clear(input);
    await user.type(input, 'https://arxiv.org/abs/2402.08954');
    await user.click(screen.getByRole('button', { name: 'Import paper' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/imports',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ url: 'https://arxiv.org/html/2402.08954' }),
      }),
    );
    expect(await screen.findByText('Formula Graph Research')).toBeTruthy();
    expect(screen.getByText(/2 equations persisted/)).toBeTruthy();
    expect(screen.getByText('Exact evidence snapshot')).toBeTruthy();
    expect(screen.getByText('Source context')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /Equation 2/ }));
    const inspector = screen.getByRole('complementary', {
      name: 'Formula inspector',
    });
    expect(within(inspector).getByText('y=2')).toBeTruthy();
    expect(within(inspector).getByText('tex annotation')).toBeTruthy();
  });

  it('keeps the current graph and shows a bounded storage error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        json: async () => ({ code: 'DATABASE_UNAVAILABLE' }),
      })),
    );
    const user = userEvent.setup();
    render(
      <ResearchWorkspace
        user={{ displayName: 'Researcher', email: 'researcher@example.com' }}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Import paper' }));

    expect(
      await screen.findByText('Workspace storage is temporarily unavailable'),
    ).toBeTruthy();
    expect(screen.getByText('Attention Is All You Need')).toBeTruthy();
  });
});
