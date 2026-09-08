// @vitest-environment jsdom

import './setup';

import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ResearchWorkspace, {
  buildEvidenceInspector,
} from '../app/research-workspace';
import type {
  EvidenceGraphEdge,
  EvidenceGraphNode,
  EvidenceGraphSnapshotResponse,
  EvidenceSearchResponse,
  WorkspaceImportResponse,
} from '../lib/import-types';

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

const completedSearch: EvidenceSearchResponse = {
  hits: [
    {
      uuid: '11111111-1111-4111-8111-111111111111',
      kind: 'Equation',
      logical_id: 'S1.E1',
      paper_id: '2402.08954',
      version: 1,
      valid_at: '2024-02-14T00:00:00Z',
      verification_status: 'reported',
      payload: { equation_id: 'S1.E1', latex: 'x=1', section: '1 Method' },
      episode_uuids: ['episode-1'],
      score: 16393,
      match_sources: ['lexical'],
      score_components: {
        lexical_rank: 1,
        semantic_rank: null,
        graph_rank: null,
        graph_distance: null,
      },
    },
  ],
  next_cursor: null,
  semantic_available: false,
};

const emptyGraph: EvidenceGraphSnapshotResponse = {
  paper: null,
  nodes: [],
  edges: [],
  truncated: false,
};

const completedGraph: EvidenceGraphSnapshotResponse = {
  paper: {
    paper_id: '2402.08954',
    version: 1,
    title: 'Formula Graph Research',
    source_url: 'https://arxiv.org/html/2402.08954v1',
    source_sha256: 'a'.repeat(64),
    updated_at: 1_708_000_000_000,
  },
  nodes: [
    {
      uuid: '11111111-1111-4111-8111-111111111111',
      kind: 'PaperVersion',
      logical_id: '2402.08954v1',
      paper_id: '2402.08954',
      version: 1,
      valid_at: '2024-02-14T00:00:00Z',
      verification_status: 'reported',
      payload: { authors: ['Ada Researcher', 'Grace Scientist'] },
      episode_uuids: ['episode-root'],
    },
    {
      uuid: '22222222-2222-4222-8222-222222222222',
      kind: 'Section',
      logical_id: 'S1',
      paper_id: '2402.08954',
      version: 1,
      valid_at: '2024-02-14T00:00:00Z',
      verification_status: 'reported',
      payload: {
        section_id: 'S1',
        title: '1 Method',
        equation_ids: ['S1.E1', 'S1.E2'],
        anchor: 'S1',
        anchor_is_source: true,
      },
      episode_uuids: ['episode-section'],
    },
    ...completedImport.paper.equations.map((equation, index) => ({
      uuid:
        index === 0
          ? '33333333-3333-4333-8333-333333333333'
          : '44444444-4444-4444-8444-444444444444',
      kind: 'Equation' as const,
      logical_id: equation.equation_id,
      paper_id: '2402.08954',
      version: 1,
      valid_at: '2024-02-14T00:00:00Z',
      verification_status: 'reported' as const,
      payload: {
        ...equation,
        ...(index === 0
          ? {
              formula_analysis: {
                status: 'well_typed',
                canonical_hash: 'b'.repeat(64),
                requires_confirmation: false,
                shape_errors: [],
                domain_errors: [],
                contracts: [
                  {
                    name: 'x',
                    category: 'scalar',
                    shape: [],
                    confidence: 0.98,
                    confirmed: true,
                  },
                ],
              },
            }
          : {}),
      },
      episode_uuids: ['episode-section'],
    })),
  ],
  edges: [
    {
      uuid: 'edge-version-section',
      source_uuid: '11111111-1111-4111-8111-111111111111',
      target_uuid: '22222222-2222-4222-8222-222222222222',
      relation: 'contains',
      source_anchor: 'S1',
      episode_uuids: ['episode-section'],
      valid_at: '2024-02-14T00:00:00Z',
    },
    {
      uuid: 'edge-section-equation-1',
      source_uuid: '22222222-2222-4222-8222-222222222222',
      target_uuid: '33333333-3333-4333-8333-333333333333',
      relation: 'contains',
      source_anchor: 'S1.E1',
      episode_uuids: ['episode-section'],
      valid_at: '2024-02-14T00:00:00Z',
    },
    {
      uuid: 'edge-section-equation-2',
      source_uuid: '22222222-2222-4222-8222-222222222222',
      target_uuid: '44444444-4444-4444-8444-444444444444',
      relation: 'contains',
      source_anchor: 'S1.E2',
      episode_uuids: ['episode-section'],
      valid_at: '2024-02-14T00:00:00Z',
    },
  ],
  truncated: false,
};

function jsonResponse(body: unknown, ok = true) {
  return { ok, json: async () => body };
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ResearchWorkspace import interaction', () => {
  it('labels the initial graph as a curated seven-equation demo', () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(emptyGraph)));
    render(
      <ResearchWorkspace
        user={{ displayName: 'Researcher', email: 'researcher@example.com' }}
      />,
    );

    expect(screen.getByText('7 equations')).toBeTruthy();
    expect(screen.getByText(/Curated demo/)).toBeTruthy();
  });

  it('restores the persisted graph on reload without a new import', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(completedGraph)));
    render(
      <ResearchWorkspace
        user={{ displayName: 'Researcher', email: 'researcher@example.com' }}
      />,
    );

    expect(await screen.findByText('Exact evidence snapshot')).toBeTruthy();
    expect(screen.getByText('4 saved nodes · 3 relations')).toBeTruthy();
    expect(screen.getByText('Formula Graph Research')).toBeTruthy();
    expect(screen.getByText('Formula identity')).toBeTruthy();
    expect(screen.getByText('well typed')).toBeTruthy();
    expect(screen.getByLabelText('Inferred symbol contracts')).toBeTruthy();
  });

  it('submits the canonical URL and replaces demo evidence with persisted formulas', async () => {
    let importFinished = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (requestUrl(input) === '/api/imports') {
        importFinished = true;
        return jsonResponse(completedImport);
      }
      if (requestUrl(input) === '/api/graph') {
        return jsonResponse(importFinished ? completedGraph : emptyGraph);
      }
      throw new Error(`Unexpected request: ${requestUrl(input)}`);
    }) as unknown as typeof fetch;
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

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/imports',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
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
      vi.fn(async (input: RequestInfo | URL) =>
        requestUrl(input) === '/api/graph'
          ? jsonResponse(emptyGraph)
          : jsonResponse({ code: 'DATABASE_UNAVAILABLE' }, false),
      ),
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

  it('opens hybrid search and renders source-bound ranked evidence', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      requestUrl(input) === '/api/graph'
        ? jsonResponse(emptyGraph)
        : jsonResponse(completedSearch),
    ) as unknown as typeof fetch;
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(
      <ResearchWorkspace
        user={{ displayName: 'Researcher', email: 'researcher@example.com' }}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Search graph' }));
    const input = screen.getByRole('textbox', {
      name: 'Formula or research concept',
    });
    await user.type(input, 'scaled attention');
    await user.click(screen.getByRole('button', { name: 'Search evidence' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/search',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/search',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ query: 'scaled attention', limit: 8 }),
      }),
    );
    const results = await screen.findByLabelText('Evidence search results');
    expect(within(results).getByText('x=1')).toBeTruthy();
    expect(within(results).getByText('lexical')).toBeTruthy();
    expect(screen.getByText(/lexical \+ graph ranking/)).toBeTruthy();

    await user.click(within(results).getByText('x=1'));
    const inspector = screen.getByRole('complementary', {
      name: 'Formula inspector',
    });
    expect(within(inspector).getByText('x=1')).toBeTruthy();
    expect(within(inspector).getByText('reported')).toBeTruthy();
  });
});

describe('provenance inspector', () => {
  const baseNode: EvidenceGraphNode = {
    uuid: '11111111-1111-4111-8111-111111111111',
    kind: 'Equation',
    logical_id: 'S1.E1',
    paper_id: '2402.08954',
    version: 1,
    valid_at: '2024-02-14T00:00:00Z',
    verification_status: 'reported',
    payload: {
      latex: 'x=1',
      anchor: 'generated-S1-E1',
      anchor_is_source: false,
      preceding_text: 'Let x be fixed.',
    },
    episode_uuids: ['episode-a', 'episode-b'],
  };

  it('marks a generated or broken anchor and links only to the paper', () => {
    const inspector = buildEvidenceInspector(
      baseNode,
      'https://arxiv.org/html/2402.08954v1',
      [baseNode],
      [],
    );

    expect(inspector.anchorIsSource).toBe(false);
    expect(inspector.sourceHref).toBe('https://arxiv.org/html/2402.08954v1');
    expect(inspector.sourceText).toBe('Let x be fixed.');
  });

  it('marks an older paper version as superseded', () => {
    const oldVersion: EvidenceGraphNode = {
      ...baseNode,
      uuid: '22222222-2222-4222-8222-222222222222',
      kind: 'PaperVersion',
      logical_id: '2402.08954v1',
    };
    const newVersion: EvidenceGraphNode = {
      ...oldVersion,
      uuid: '33333333-3333-4333-8333-333333333333',
      logical_id: '2402.08954v2',
      version: 2,
    };
    const edge: EvidenceGraphEdge = {
      uuid: 'supersedes-edge',
      source_uuid: newVersion.uuid,
      target_uuid: oldVersion.uuid,
      relation: 'supersedes',
      source_anchor: '',
      episode_uuids: ['episode-new'],
      valid_at: '2024-03-01T00:00:00Z',
    };

    const inspector = buildEvidenceInspector(
      oldVersion,
      'https://arxiv.org/html/2402.08954v2',
      [oldVersion, newVersion],
      [edge],
    );
    expect(inspector.superseded).toBe(true);
    expect(inspector.relations).toEqual([
      expect.objectContaining({
        direction: 'incoming',
        relation: 'supersedes',
        neighbor: 'PaperVersion · 2402.08954v2',
      }),
    ]);
  });

  it('retains every supporting episode on a node and its relation', () => {
    const neighbor: EvidenceGraphNode = {
      ...baseNode,
      uuid: '44444444-4444-4444-8444-444444444444',
      logical_id: 'S1.E2',
    };
    const edge: EvidenceGraphEdge = {
      uuid: 'evidence-edge',
      source_uuid: baseNode.uuid,
      target_uuid: neighbor.uuid,
      relation: 'derived_from',
      source_anchor: 'S1.E1',
      episode_uuids: ['episode-a', 'episode-c'],
      valid_at: '2024-02-14T00:00:00Z',
    };
    const inspector = buildEvidenceInspector(
      baseNode,
      'https://arxiv.org/html/2402.08954v1',
      [baseNode, neighbor],
      [edge],
    );

    expect(inspector.episodeIds).toEqual(['episode-a', 'episode-b']);
    expect(inspector.relations[0].episodeCount).toBe(2);
  });
});
