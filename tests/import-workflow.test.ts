import { describe, expect, it, vi } from 'vitest';
import type { WorkspaceImportResponse } from '../lib/import-types';
import {
  HttpGraphImportClient,
  type ImportErrorCode,
  type ImportRepository,
  ImportWorkflowError,
  type PersistedPaper,
  resolveGraphApiConfiguration,
  runPaperImport,
} from '../lib/server/paper-import';

const graphResponse = {
  paper: {
    paper_id: '2402.08954',
    version: 1,
    title: 'Graphiti for formulas',
    authors: ['Ada Researcher'],
    version_published_at: '2024-02-14',
    metadata_warnings: [],
    source_url: 'https://arxiv.org/html/2402.08954v1',
    source_sha256: 'a'.repeat(64),
    sections: [
      {
        section_id: 'S1',
        anchor: 'S1',
        anchor_is_source: true,
        title: 'Method',
        order: 1,
        parent_section_id: null,
        text: 'A source-bound method.',
        equation_ids: ['S1.E1'],
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
        section: 'Method',
        section_id: 'S1',
        preceding_text: 'Define x.',
        following_text: null,
        extraction_method: 'alttext',
        confidence: 0.98,
      },
    ],
  },
  receipt: {
    import_uuid: 'import-1',
    node_count: 4,
    edge_count: 3,
    episode_count: 2,
    replayed: false,
  },
};

class MemoryRepository implements ImportRepository {
  workspace: { id: string; ownerUserId: string } | null = null;
  job: { id: string; workspaceId: string; requestedBy: string } | null = null;
  succeeded: PersistedPaper | null = null;
  failed: ImportErrorCode | null = null;

  async ensureWorkspace(input: {
    id: string;
    ownerUserId: string;
    now: number;
  }) {
    this.workspace = input;
  }

  async createJob(input: {
    id: string;
    workspaceId: string;
    requestedBy: string;
    canonicalUrl: string;
    now: number;
  }) {
    this.job = input;
  }

  async markSucceeded(input: {
    jobId: string;
    requestedBy: string;
    paper: PersistedPaper;
  }) {
    this.succeeded = input.paper;
  }

  async markFailed(input: {
    jobId: string;
    workspaceId: string;
    requestedBy: string;
    errorCode: ImportErrorCode;
    now: number;
  }) {
    this.failed = input.errorCode;
  }
}

describe('authenticated paper import workflow', () => {
  it('derives the tenant server-side and persists a successful exact import', async () => {
    const repository = new MemoryRepository();
    let upstreamBody: Record<string, unknown> | undefined;
    const fetchImplementation = vi.fn(async (_input, init) => {
      upstreamBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return Response.json(graphResponse);
    }) as unknown as typeof fetch;
    const client = new HttpGraphImportClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-secret',
      },
      fetchImplementation,
    );

    const result: WorkspaceImportResponse = await runPaperImport({
      canonicalUrl: 'https://arxiv.org/html/2402.08954',
      userId: 'site-scoped-user-id',
      repository,
      graphClient: client,
      now: () => 1_725_000_000_000,
      createId: () => 'fixed-job-id',
    });

    expect(repository.workspace?.id).toMatch(/^ws_[a-f0-9]{48}$/);
    expect(upstreamBody?.workspace_id).toBe(repository.workspace?.id);
    expect(String(upstreamBody?.workspace_id)).not.toContain(
      'site-scoped-user-id',
    );
    expect(repository.job).toMatchObject({
      id: 'job_fixed-job-id',
      requestedBy: 'site-scoped-user-id',
    });
    expect(repository.succeeded).toMatchObject({
      arxivId: '2402.08954',
      version: 1,
      sourceSha256: 'a'.repeat(64),
    });
    expect(repository.failed).toBeNull();
    expect(result.paper.equations[0].anchor).toBe('S1.E1');
    expect(fetchImplementation).toHaveBeenCalledWith(
      new URL('https://graph.example/v1/imports'),
      expect.objectContaining({ redirect: 'manual' }),
    );
  });

  it('never follows a Graph API redirect with the service token', async () => {
    const client = new HttpGraphImportClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-secret',
      },
      vi.fn(async () =>
        new Response(null, {
          status: 302,
          headers: { location: 'https://unexpected.example/import' },
        })) as unknown as typeof fetch,
    );

    await expect(
      client.importEvidence('https://arxiv.org/html/2402.08954', 'ws_safe'),
    ).rejects.toMatchObject({ code: 'GRAPH_API_UNAVAILABLE', status: 502 });
  });

  it('records a bounded error code when extraction fails', async () => {
    const repository = new MemoryRepository();
    const graphClient = {
      async importEvidence() {
        throw new ImportWorkflowError('EXTRACTION_FAILED', 422);
      },
    };

    await expect(
      runPaperImport({
        canonicalUrl: 'https://arxiv.org/html/2402.08954',
        userId: 'user-2',
        repository,
        graphClient,
        now: () => 1,
        createId: () => 'failed-job',
      }),
    ).rejects.toMatchObject({ code: 'EXTRACTION_FAILED', status: 422 });
    expect(repository.failed).toBe('EXTRACTION_FAILED');
    expect(repository.succeeded).toBeNull();
  });

  it('rejects an unpinned or structurally invalid Graph API response', async () => {
    const unpinned = structuredClone(graphResponse);
    unpinned.paper.source_url = 'https://arxiv.org/html/2402.08954';
    const client = new HttpGraphImportClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-secret',
      },
      vi.fn(async () => Response.json(unpinned)) as unknown as typeof fetch,
    );

    await expect(
      client.importEvidence('https://arxiv.org/html/2402.08954', 'ws_safe'),
    ).rejects.toMatchObject({
      code: 'GRAPH_API_INVALID_RESPONSE',
      status: 502,
    });
  });

  it('rejects oversized Graph API responses after decompression', async () => {
    const client = new HttpGraphImportClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-secret',
      },
      vi.fn(async () => new Response('x'.repeat(2 * 1024 * 1024 + 1), { status: 200 })) as unknown as typeof fetch,
    );

    await expect(
      client.importEvidence('https://arxiv.org/html/2402.08954', 'ws_safe'),
    ).rejects.toMatchObject({ code: 'GRAPH_API_RESPONSE_TOO_LARGE' });
  });
});

describe('Graph API configuration policy', () => {
  it('requires HTTPS and a service token in production', () => {
    expect(
      resolveGraphApiConfiguration(
        {
          GRAPH_API_URL: 'http://graph.example',
          GRAPH_API_SERVICE_TOKEN: 'secret',
        },
        true,
      ),
    ).toBeNull();
    expect(
      resolveGraphApiConfiguration(
        { GRAPH_API_URL: 'https://graph.example' },
        true,
      ),
    ).toBeNull();
  });

  it('allows only a loopback HTTP service during development', () => {
    expect(
      resolveGraphApiConfiguration(
        { GRAPH_API_SERVICE_TOKEN: 'secret' },
        false,
      )?.baseUrl.href,
    ).toBe('http://127.0.0.1:8000/');
    expect(
      resolveGraphApiConfiguration(
        {
          GRAPH_API_URL: 'http://192.168.1.25:8000',
          GRAPH_API_SERVICE_TOKEN: 'secret',
        },
        false,
      ),
    ).toBeNull();
  });
});
