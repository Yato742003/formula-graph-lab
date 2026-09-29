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
                status: 'analyzed',
                canonical_hash: 'b'.repeat(64),
                requires_review: false,
                shape_errors: [],
                domain_assessment: {
                  status: 'discharged',
                  obligations: [],
                  contradictions: [],
                },
                contracts: [
                  {
                    name: 'x',
                    category: 'scalar',
                    shape: [],
                    inference_confidence: 0.98,
                    review_required: false,
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
  it('routes the primary navigation to existing paper, graph, and research views', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(emptyGraph)));
    const user = userEvent.setup();
    const { container } = render(
      <ResearchWorkspace
        user={{ displayName: 'Researcher', email: 'researcher@example.com' }}
      />,
    );
    const navigation = screen.getByRole('navigation', {
      name: 'Primary navigation',
    });
    const papers = within(navigation).getByRole('button', { name: 'Papers' });
    const graph = within(navigation).getByRole('button', { name: 'Graph' });
    const research = within(navigation).getByRole('button', {
      name: 'Research',
    });
    const workspace = container.querySelector('.research-grid');

    expect(research.getAttribute('aria-current')).toBe('page');
    await user.click(papers);
    expect(papers.getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('tab', { name: /Lineage Graph/i }).getAttribute('aria-selected')).toBe('true');
    expect(workspace?.classList.contains('is-left-collapsed')).toBe(false);

    await user.click(graph);
    expect(graph.getAttribute('aria-current')).toBe('page');
    expect(workspace?.classList.contains('is-left-collapsed')).toBe(true);

    await user.click(research);
    expect(research.getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('heading', { name: 'Start with your research question' })).toBeTruthy();
    expect(workspace?.classList.contains('is-spec-view')).toBe(true);
  });

  it('labels the curated seven-equation demo when opening lineage', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(emptyGraph)),
    );
    const user = userEvent.setup();
    const { container } = render(
      <ResearchWorkspace
        user={{ displayName: 'Researcher', email: 'researcher@example.com' }}
      />,
    );
    await user.click(screen.getByRole('tab', { name: /Lineage Graph/i }));

    expect(screen.getByText('7 equations')).toBeTruthy();
    expect(screen.getByText(/Curated demo/)).toBeTruthy();
    expect(container.querySelector('.react-flow__attribution')).toBeTruthy();
  });

  it('restores the persisted graph on reload without a new import', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(completedGraph)),
    );
    render(
      <ResearchWorkspace
        user={{ displayName: 'Researcher', email: 'researcher@example.com' }}
      />,
    );
    await userEvent.setup().click(screen.getByRole('tab', { name: /Lineage Graph/i }));

    expect(await screen.findByText('Exact evidence snapshot')).toBeTruthy();
    expect(screen.getByText('4 saved nodes · 3 relations')).toBeTruthy();
    expect(screen.getByText('Formula Graph Research')).toBeTruthy();
    expect(screen.getByText('Formula identity')).toBeTruthy();
    expect(screen.getByText(/Saved paper evidence loaded/)).toBeTruthy();
    expect(screen.getByText('analyzed')).toBeTruthy();
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
    await user.click(screen.getByRole('tab', { name: /Lineage Graph/i }));

    const input = screen.getByRole('textbox', {
      name: /arXiv HTML paper URL/i,
    });
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

    await user.click(screen.getByRole('tab', { name: /Lineage Graph/i }));
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
    expect(inspector.confidence).toBeNull();
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

  it('collapses and expands the left source panel', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <ResearchWorkspace user={{ displayName: 'Test User', email: 'test@example.com' }} />,
    );
    await user.click(screen.getByRole('tab', { name: /Lineage Graph/i }));

    const grid = container.querySelector('.research-grid');
    const sourcePanel = container.querySelector('.source-panel');
    expect(grid).toBeTruthy();
    expect(grid?.classList.contains('is-left-collapsed')).toBe(false);
    expect(sourcePanel?.classList.contains('is-collapsed')).toBe(false);

    // Collapse left sidebar
    const collapseLeftBtn = screen.getByRole('button', {
      name: /collapse paper sources/i,
    });
    await user.click(collapseLeftBtn);

    expect(grid?.classList.contains('is-left-collapsed')).toBe(true);
    expect(sourcePanel?.classList.contains('is-collapsed')).toBe(true);

    // Expand button appears in graph-header
    const expandSourcesBtn = screen.getByRole('button', {
      name: /expand paper sources/i,
    });
    expect(expandSourcesBtn).toBeTruthy();

    // Click expand button to restore
    await user.click(expandSourcesBtn);
    expect(grid?.classList.contains('is-left-collapsed')).toBe(false);
    expect(sourcePanel?.classList.contains('is-collapsed')).toBe(false);
  });

  it('collapses and expands the right inspector panel', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <ResearchWorkspace user={{ displayName: 'Test User', email: 'test@example.com' }} />,
    );
    await user.click(screen.getByRole('tab', { name: /Lineage Graph/i }));

    const grid = container.querySelector('.research-grid');
    const inspectorPanel = container.querySelector('.inspector-panel');
    expect(grid).toBeTruthy();
    expect(grid?.classList.contains('is-right-collapsed')).toBe(false);
    expect(inspectorPanel?.classList.contains('is-collapsed')).toBe(false);

    // Collapse right sidebar
    const collapseRightBtn = screen.getByRole('button', {
      name: /collapse inspector/i,
    });
    await user.click(collapseRightBtn);

    expect(grid?.classList.contains('is-right-collapsed')).toBe(true);
    expect(inspectorPanel?.classList.contains('is-collapsed')).toBe(true);

    // Expand button appears in graph-header
    const expandInspectorBtn = screen.getByRole('button', {
      name: /expand inspector/i,
    });
    expect(expandInspectorBtn).toBeTruthy();

    // Click expand button to restore
    await user.click(expandInspectorBtn);
    expect(grid?.classList.contains('is-right-collapsed')).toBe(false);
    expect(inspectorPanel?.classList.contains('is-collapsed')).toBe(false);
  });

  it('opens the editable ProblemSpec panel without fabricated artifact inputs', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
      requestUrl(input) === '/api/graph'
        ? jsonResponse(emptyGraph) : jsonResponse({ items: [], total: 0, indexed_papers: [] }),
    ));
    const user = userEvent.setup();
    const { container } = render(<ResearchWorkspace user={{ displayName: 'Researcher', email: 'researcher@example.com' }} />);
    const specTab = screen.getByRole('tab', { name: /Problem Spec \(G1\)/i });
    expect(specTab.getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('heading', { name: 'Start with your research question' })).toBeTruthy();
    expect(container.querySelector('.research-grid')?.classList.contains('is-spec-view')).toBe(true);
    expect(container.querySelector('[aria-label="Import a paper"]')).toBeNull();
    expect(screen.getByLabelText('Research question')).toHaveProperty('value', '');
    expect(screen.getByRole('button', { name: 'Continue to manifest' })).toHaveProperty('disabled', false);
    expect(screen.queryByRole('button', { name: 'Freeze Spec' })).toBeNull();
    await user.click(screen.getByRole('tab', { name: /Lineage Graph/i }));
    expect(container.querySelector('.research-grid')?.classList.contains('is-spec-view')).toBe(false);
    expect(container.querySelector('[aria-label="Import a paper"]')).toBeTruthy();
  });

  it('keeps the research workflow stage and hypothesis boundary visible', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
      requestUrl(input) === '/api/graph'
        ? jsonResponse(emptyGraph)
        : jsonResponse({ items: [], total: 0, indexed_papers: [] }),
    ));
    const user = userEvent.setup();
    render(
      <ResearchWorkspace
        user={{ displayName: 'Researcher', email: 'researcher@example.com' }}
      />,
    );

    const workflow = screen.getByRole('navigation', {
      name: 'Research workflow',
    });
    expect(within(workflow).getByText('Step 1 of 3 · draft scope')).toBeTruthy();
    expect(
      within(workflow)
        .getByRole('button', { name: /Define scope/i })
        .getAttribute('aria-current'),
    ).toBe('step');
    expect(within(workflow).getByText('Hypothesis only')).toBeTruthy();

    await user.click(within(workflow).getByRole('button', { name: /Trace evidence/i }));
    expect(within(workflow).getByText('Step 2 of 3 · trace evidence')).toBeTruthy();
    expect(
      screen.getByRole('tab', { name: /Lineage Graph/i }).getAttribute('aria-selected'),
    ).toBe('true');

    await user.click(within(workflow).getByRole('button', { name: /Verify ports/i }));
    expect(within(workflow).getByText('Step 3 of 3 · verify ports')).toBeTruthy();
    expect(
      screen
        .getByRole('tab', { name: /Compatibility \(G3\)/i })
        .getAttribute('aria-selected'),
    ).toBe('true');

    await user.click(within(workflow).getByRole('button', { name: 'Open research move' }));
    expect(within(workflow).getByText('Step 2 of 3 · trace evidence')).toBeTruthy();
    expect(
      screen.getByRole('tab', { name: /Lineage Graph/i }).getAttribute('aria-selected'),
    ).toBe('true');
  });

  it('preserves compatibility when the server returns an invalid review receipt', async () => {
    let reviewBody: Record<string, unknown> | undefined;
    const reviewKeys: Array<string | null> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/graph') return jsonResponse(emptyGraph);
      if (url === '/api/research/lineage?limit=100') return jsonResponse({ items: [], total: 0 });
      if (url === '/api/research/lineage/coverage') return jsonResponse({ indexed_papers: [] });
      if (url === '/api/research/compatibility?limit=100') return jsonResponse({
        total: 1,
        items: [{
          mapping: {
            mapping_id: 'map_saved', explicit_binding_reviewed: false,
            producer_port: { equation_id: 'eq_a', symbol_name: 'x', domain: 'real', shape: [] },
            consumer_port: { equation_id: 'eq_b', symbol_name: 'y', domain: 'real', shape: [] },
          },
          assessment: {
            mapping_id: 'map_saved', status: 'unknown', freshness: 'current',
            policy_version: 'compatibility-policy.v3', reasons: [],
            unresolved_requirements: ['unbound_symbol_scope'],
          },
        }],
      });
      if (url.includes('/api/research/compatibility/')) {
        reviewKeys.push(new Headers(init?.headers).get('x-idempotency-key'));
        reviewBody = JSON.parse(init?.body as string) as Record<string, unknown>;
        return jsonResponse({ status: 'ok' });
      }
      throw new Error(`Unexpected request: ${url}`);
    }) as unknown as typeof fetch;
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(
      <ResearchWorkspace user={{ displayName: 'Researcher', email: 'researcher@example.com' }} />,
    );

    // Switch to Compatibility (G3) tab
    const compatTab = screen.getByRole('tab', { name: /Compatibility \(G3\)/i });
    await user.click(compatTab);
    expect(compatTab.getAttribute('aria-selected')).toBe('true');

    // Find UNKNOWN mapping review button
    const reviewBtn = await screen.findByRole('button', { name: /Review Binding/i });
    await user.click(reviewBtn);
    expect(reviewKeys).toHaveLength(0);
    await user.type(screen.getByRole('textbox', { name: /Binding review rationale/i }),
      'I checked the x to y symbol binding against the stored contracts.');
    await user.click(reviewBtn);

    expect(reviewBody?.decision).toBe('reviewed');
    expect(reviewBody?.notes).toBe('I checked the x to y symbol binding against the stored contracts.');
    expect(await screen.findByText(/Could not confirm the review/)).toBeTruthy();
    expect(screen.queryByText('human_binding_approved')).toBeNull();
    expect(screen.getByText('unbound_symbol_scope')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: /Review Binding/i }));
    await waitFor(() => expect(reviewKeys).toHaveLength(2));
    expect(reviewKeys[0]).toBeTruthy();
    expect(reviewKeys[1]).toBe(reviewKeys[0]);
  });

  it('renders persisted multi-paper coverage indicators in the sidebar', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/graph') return jsonResponse(emptyGraph);
      if (url === '/api/research/lineage/coverage') return jsonResponse({
        indexed_papers: [
          { paper_id: 'paper-a', version: 1, title: 'Source A', has_html: true, equation_count: 14 },
          { paper_id: 'paper-b', version: 1, title: 'Source B', has_html: true, equation_count: 22 },
          { paper_id: 'paper-c', version: null, title: 'Source C', has_html: false, equation_count: 0 },
        ],
      });
      return jsonResponse({ items: [], total: 0 });
    }));
    render(
      <ResearchWorkspace user={{ displayName: 'Researcher', email: 'researcher@example.com' }} />,
    );

    expect(await screen.findByText('Multi-paper lineage (3 indexed)')).toBeTruthy();
    expect(screen.getByText('HTML (14 eq)')).toBeTruthy();
    expect(screen.getByText('HTML (22 eq)')).toBeTruthy();
    expect(screen.getByText('Metadata Only')).toBeTruthy();
    expect(screen.getByText(/arXiv metadata only — equations not extracted/i)).toBeTruthy();
  });

  it('opens the exact workspace source context for a lineage assertion', async () => {
    const sourceHash = 'a'.repeat(64);
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/graph') return jsonResponse({ ...emptyGraph, paper: {
        paper_id: '2303.11366', version: 4, title: 'A persisted paper without equations',
        source_url: 'https://arxiv.org/html/2303.11366v4', source_sha256: sourceHash,
        updated_at: 1_708_000_000_000,
      } });
      if (url === '/api/research/lineage/coverage') return jsonResponse({ indexed_papers: [] });
      if (url === '/api/research/compatibility?limit=100') return jsonResponse({ items: [], total: 0 });
      if (url === '/api/research/lineage?limit=100') return jsonResponse({ total: 1, items: [{
        assertion_id: 'assertion-1', relation_type: 'mathematical_derivation', status: 'asserted',
        source: { id: 'eq-a', version: 1 }, target: { id: 'eq-b', version: 1 },
        evidence: [{ source_entity_id: 'source-1', anchor: 'S1.E1', source_hash: sourceHash }],
        description: 'A human-authored derivation assertion',
      }] });
      if (url === '/api/research/lineage/sources?source_id=source-1') return jsonResponse({ items: [{
        id: 'source-1', kind: 'equation', paper_id: '1706.03762', version: 1,
        anchor: 'S1.E1', text: 'Exact stored source context', latex: 'x=y', source_hash: sourceHash,
      }] });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup();
    render(<ResearchWorkspace user={{ displayName: 'Researcher', email: 'researcher@example.com' }} />);
    await user.click(screen.getByRole('tab', { name: /Lineage Graph/i }));
    expect(await screen.findByText('No formula selected')).toBeTruthy();
    expect(screen.getByText('No equation evidence in this snapshot')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Paper snapshot' })).toBeTruthy();
    await user.click(await screen.findByRole('button', { name: /mathematical derivation/i }));
    expect(await screen.findByText('Exact stored source context')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Open HTML anchor/i }).getAttribute('href'))
      .toBe('https://arxiv.org/html/1706.03762v1#S1.E1');
  });

  it('reloads a stale historical assessment without a success badge', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/graph') return jsonResponse(emptyGraph);
      if (url === '/api/research/lineage/coverage') return jsonResponse({ indexed_papers: [] });
      if (url === '/api/research/lineage?limit=100') return jsonResponse({ items: [], total: 0 });
      if (url === '/api/research/compatibility?limit=100') return jsonResponse({ total: 2, items: [{
        mapping: { mapping_id: 'old-map', explicit_binding_reviewed: true,
          producer_port: { equation_id: 'eq-a', symbol_name: 'x', domain: 'real', shape: [] },
          consumer_port: { equation_id: 'eq-b', symbol_name: 'y', domain: 'real', shape: [] } },
        assessment: { mapping_id: 'old-map', status: 'compatible', freshness: 'stale',
          policy_version: 'compatibility-policy.v2', reasons: ['policy_version_changed'],
          unresolved_requirements: [] },
      }, {
        mapping: { mapping_id: 'stale-unknown', explicit_binding_reviewed: false,
          producer_port: { equation_id: 'eq-a', symbol_name: 'x', domain: 'real', shape: [] },
          consumer_port: { equation_id: 'eq-b', symbol_name: 'y', domain: 'real', shape: [] } },
        assessment: { mapping_id: 'stale-unknown', status: 'unknown', freshness: 'stale',
          policy_version: 'compatibility-policy.v2', reasons: ['stale_dependency'],
          unresolved_requirements: ['unbound_symbol_scope'] },
      }] });
      return jsonResponse({ items: [], total: 0 });
    }));
    const user = userEvent.setup();
    const mount = () => render(<ResearchWorkspace user={{ displayName: 'Researcher', email: 'researcher@example.com' }} />);
    const first = mount();
    await user.click(screen.getByRole('tab', { name: /Compatibility \(G3\)/i }));
    expect(first.container.querySelector('.research-grid')?.classList.contains('is-compat-view')).toBe(true);
    const badge = await screen.findByText('historical compatible');
    expect(badge).toBeTruthy();
    expect(badge.className).not.toContain('bg-emerald-600');
    expect(screen.getAllByText('compatibility-policy.v2')).toHaveLength(2);
    expect(screen.queryByText(/^compatible$/)).toBeNull();
    expect(screen.getByText('historical unknown')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Review Binding/i })).toBeNull();
    expect(screen.queryByRole('textbox', { name: /Binding review rationale/i })).toBeNull();
    first.unmount();
    const second = mount();
    await user.click(screen.getByRole('tab', { name: /Compatibility \(G3\)/i }));
    expect(second.container.querySelector('.research-grid')?.classList.contains('is-compat-view')).toBe(true);
    const remountBadge = await screen.findByText('historical compatible');
    expect(remountBadge).toBeTruthy();
    expect(remountBadge.className).not.toContain('bg-emerald-600');
    await user.click(screen.getByRole('tab', { name: /Lineage Graph/i }));
    expect(second.container.querySelector('.research-grid')?.classList.contains('is-compat-view')).toBe(false);
  });

  it('does not invent research evidence when loading fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
      requestUrl(input) === '/api/graph'
        ? jsonResponse(emptyGraph) : new Response('{}', { status: 503 }),
    ));
    const user = userEvent.setup();
    render(<ResearchWorkspace user={{ displayName: 'Researcher', email: 'researcher@example.com' }} />);
    expect(await screen.findByText(/curated demo remains illustrative only/i)).toBeTruthy();
    await user.click(screen.getByRole('tab', { name: /Lineage Graph/i }));
    const graphStatus = document.querySelector('.graph-status');
    expect(graphStatus?.tagName).toBe('OUTPUT');
    expect(graphStatus?.getAttribute('aria-live')).toBe('polite');
    expect(graphStatus?.textContent).toContain('Version-aware demo');
    expect(graphStatus?.textContent).toContain('No saved graph yet');
    expect(screen.queryByText('Foundational Transformer Architecture')).toBeNull();
    expect(screen.queryByText('identity_tensor_binding')).toBeNull();
  });
});
