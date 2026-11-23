import { afterEach, describe, expect, it, vi } from 'vitest';

const routeMocks = vi.hoisted(() => ({
  user: vi.fn(),
  d1First: vi.fn(),
  d1Bind: vi.fn(),
  d1Prepare: vi.fn(),
}));

vi.mock('@/app/chatgpt-auth', () => ({ getChatGPTUser: routeMocks.user }));
vi.mock('@/db', () => ({
  getD1: () => ({
    prepare: (statement: string) => {
      routeMocks.d1Prepare(statement);
      return {
        bind: (...bound: unknown[]) => {
          routeMocks.d1Bind(...bound);
          return {
            first: () => routeMocks.d1First(),
          };
        },
      };
    },
  }),
}));
vi.mock('cloudflare:workers', () => ({
  env: {
    APP_ENV: 'development',
    GRAPH_API_URL: 'http://localhost:8000',
    GRAPH_API_SERVICE_TOKEN: 'service-test-secret-with-at-least-32-chars',
  },
}));

import { GET } from '../app/api/graph/route';
import {
  D1GraphSnapshotRepository,
  HttpGraphSnapshotClient,
} from '../lib/server/evidence-graph';

const paper = {
  paper_id: '1706.03762',
  version: 7,
  title: 'Attention Is All You Need',
  source_url: 'https://arxiv.org/html/1706.03762v7',
  source_sha256: 'a'.repeat(64),
  updated_at: 1_725_000_000_000,
};

const graphResponse = {
  nodes: [
    {
      uuid: '11111111-1111-4111-8111-111111111111',
      kind: 'Paper',
      logical_id: '1706.03762',
      paper_id: '1706.03762',
      version: null,
      valid_at: '2023-08-02T00:00:00Z',
      verification_status: 'reported',
      payload: { arxiv_id: '1706.03762' },
      episode_uuids: ['episode-paper'],
    },
    {
      uuid: '22222222-2222-4222-8222-222222222222',
      kind: 'Equation',
      logical_id: 'S3.E1',
      paper_id: '1706.03762',
      version: 7,
      valid_at: '2023-08-02T00:00:00Z',
      verification_status: 'reported',
      payload: { equation_id: 'S3.E1', latex: 'softmax(QK^T)V' },
      episode_uuids: ['episode-section'],
    },
  ],
  edges: [
    {
      uuid: 'edge-1',
      source_uuid: '11111111-1111-4111-8111-111111111111',
      target_uuid: '22222222-2222-4222-8222-222222222222',
      relation: 'contains',
      source_anchor: 'S3.E1',
      episode_uuids: ['episode-section'],
      valid_at: '2023-08-02T00:00:00Z',
    },
  ],
  truncated: false,
};

describe('D1 graph snapshot selection', () => {
  it('selects only the latest ready paper owned by the signed-in user', async () => {
    let sql = '';
    let values: unknown[] = [];
    const database = {
      prepare(statement: string) {
        sql = statement;
        return {
          bind(...bound: unknown[]) {
            values = bound;
            return this;
          },
          async first() {
            return paper;
          },
        };
      },
    };
    const repository = new D1GraphSnapshotRepository(
      database as unknown as D1Database,
    );

    await expect(repository.latestOwnedPaper('ws_owned', 'user-a')).resolves.toEqual(
      paper,
    );
    expect(sql).toContain("w.owner_user_id = ?");
    expect(sql).toContain("p.status = 'ready'");
    expect(sql).toContain('ORDER BY p.updated_at DESC');
    expect(values).toEqual(['ws_owned', 'user-a']);
  });

  it('filters ownedPaper by optional version when provided', async () => {
    let sql = '';
    let values: unknown[] = [];
    const database = {
      prepare(statement: string) {
        sql = statement;
        return {
          bind(...bound: unknown[]) {
            values = bound;
            return this;
          },
          async first() {
            return { ...paper, version: 5, source_url: 'https://arxiv.org/html/1706.03762v5' };
          },
        };
      },
    };
    const repository = new D1GraphSnapshotRepository(
      database as unknown as D1Database,
    );

    const matched = await repository.ownedPaper('ws_owned', 'user-a', '1706.03762', 5);
    expect(matched?.version).toBe(5);
    expect(sql).toContain('p.arxiv_id = ? AND p.version = ?');
    expect(values).toEqual(['ws_owned', 'user-a', '1706.03762', 5]);

    await expect(repository.ownedPaper('ws_owned', 'user-a', '1706.03762', 0)).resolves.toBeNull();
  });
});

describe('GET /api/graph provenance and query validation', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('rejects invalid paper_id and version query parameters with 400 before querying D1', async () => {
    routeMocks.user.mockResolvedValue({ userId: 'user-a' });
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);

    const badPaper = await GET(new Request('https://app.test/api/graph?paper_id=not-an-arxiv-id'));
    expect(badPaper.status).toBe(400);
    await expect(badPaper.json()).resolves.toEqual({ code: 'INVALID_PAPER_ID' });

    const emptyPaper = await GET(new Request('https://app.test/api/graph?paper_id='));
    expect(emptyPaper.status).toBe(400);
    await expect(emptyPaper.json()).resolves.toEqual({ code: 'INVALID_PAPER_ID' });

    const badVersionZero = await GET(new Request('https://app.test/api/graph?paper_id=1706.03762&version=0'));
    expect(badVersionZero.status).toBe(400);
    await expect(badVersionZero.json()).resolves.toEqual({ code: 'INVALID_PAPER_VERSION' });

    const badVersionAlpha = await GET(new Request('https://app.test/api/graph?paper_id=1706.03762&version=v2'));
    expect(badVersionAlpha.status).toBe(400);
    await expect(badVersionAlpha.json()).resolves.toEqual({ code: 'INVALID_PAPER_VERSION' });

    expect(routeMocks.d1Prepare).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('fails closed with 404 PAPER_NOT_FOUND for unowned or unimported paper_id and never fabricates zeroed hashes', async () => {
    routeMocks.user.mockResolvedValue({ userId: 'user-a' });
    routeMocks.d1First.mockResolvedValue(null);
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);

    const response = await GET(
      new Request('https://app.test/api/graph?paper_id=2402.08954&version=2&title=Spoofed+Title'),
    );
    expect(response.status).toBe(404);
    const bodyText = await response.text();
    expect(JSON.parse(bodyText)).toEqual({ code: 'PAPER_NOT_FOUND' });
    expect(bodyText).not.toContain('0'.repeat(64));
    expect(bodyText).not.toContain('Spoofed');
    expect(fetcher).not.toHaveBeenCalled();

    const emptyDefault = await GET(new Request('https://app.test/api/graph'));
    expect(emptyDefault.status).toBe(200);
    await expect(emptyDefault.json()).resolves.toEqual({
      paper: null,
      nodes: [],
      edges: [],
      truncated: false,
    });
  });

  it('ignores title query param and queries D1 with the validated paper_id and version', async () => {
    routeMocks.user.mockResolvedValue({ userId: 'user-a' });
    routeMocks.d1First.mockResolvedValue(paper);
    const fetcher = vi.fn(async () => Response.json(graphResponse));
    vi.stubGlobal('fetch', fetcher);

    const response = await GET(
      new Request('https://app.test/api/graph?paper_id=1706.03762&version=7&title=Injected+Title'),
    );
    expect(response.status).toBe(200);
    const payload = (await response.json()) as { paper: typeof paper };
    expect(payload.paper.title).toBe('Attention Is All You Need');
    expect(payload.paper.source_sha256).toBe('a'.repeat(64));
    expect(routeMocks.d1Bind).toHaveBeenCalledWith(expect.any(String), 'user-a', '1706.03762', 7);
    expect(fetcher).toHaveBeenCalledOnce();
  });
});

describe('Graph API snapshot client', () => {
  it('uses the server-derived tenant and accepts a closed exact-evidence graph', async () => {
    let upstreamBody: Record<string, unknown> | undefined;
    const fetchImplementation = vi.fn(async (_input, init) => {
      upstreamBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return Response.json(graphResponse);
    }) as unknown as typeof fetch;
    const client = new HttpGraphSnapshotClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-test-secret-with-at-least-32-chars',
      },
      fetchImplementation,
    );

    const result = await client.load('ws_server_derived', paper, 'user-1');

    expect(upstreamBody).toEqual({
      workspace_id: 'ws_server_derived',
      paper_id: '1706.03762',
      version: 7,
    });
    expect(fetchImplementation).toHaveBeenCalledWith(
      new URL('https://graph.example/v1/graphs/snapshot'),
      expect.objectContaining({ redirect: 'manual' }),
    );
    expect(result.nodes).toHaveLength(2);
    expect(result.edges[0].relation).toBe('contains');
  });

  it('accepts bounded historical version nodes for provenance history', async () => {
    const withHistory = {
      ...structuredClone(graphResponse),
      nodes: [
        ...structuredClone(graphResponse.nodes),
        {
          uuid: '33333333-3333-4333-8333-333333333333',
          kind: 'PaperVersion',
          logical_id: '1706.03762v6',
          paper_id: '1706.03762',
          version: 6,
          valid_at: '2022-07-01T00:00:00Z',
          verification_status: 'reported',
          payload: { arxiv_id: '1706.03762v6' },
          episode_uuids: ['episode-v6'],
        },
      ],
      edges: [
        ...structuredClone(graphResponse.edges),
        {
          uuid: 'supersedes-v6',
          source_uuid: '22222222-2222-4222-8222-222222222222',
          target_uuid: '33333333-3333-4333-8333-333333333333',
          relation: 'supersedes',
          source_anchor: '',
          episode_uuids: ['episode-section'],
          valid_at: '2023-08-02T00:00:00Z',
        },
      ],
    };
    const client = new HttpGraphSnapshotClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-test-secret-with-at-least-32-chars',
      },
      vi.fn(async () => Response.json(withHistory)) as unknown as typeof fetch,
    );

    const result = await client.load('ws_safe', paper, 'user-1');
    expect(result.nodes.at(-1)?.version).toBe(6);
    expect(result.edges.at(-1)?.relation).toBe('supersedes');
  });

  it('rejects cross-version nodes and dangling edges from the upstream service', async () => {
    const crossVersion = structuredClone(graphResponse);
    crossVersion.nodes[1].version = 6;
    const crossVersionClient = new HttpGraphSnapshotClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-test-secret-with-at-least-32-chars',
      },
      vi.fn(async () => Response.json(crossVersion)) as unknown as typeof fetch,
    );
    await expect(crossVersionClient.load('ws_safe', paper, 'user-1')).rejects.toMatchObject({
      code: 'GRAPH_API_INVALID_RESPONSE',
      status: 502,
    });

    const dangling = structuredClone(graphResponse);
    dangling.edges[0].target_uuid = '33333333-3333-4333-8333-333333333333';
    const danglingClient = new HttpGraphSnapshotClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-test-secret-with-at-least-32-chars',
      },
      vi.fn(async () => Response.json(dangling)) as unknown as typeof fetch,
    );
    await expect(danglingClient.load('ws_safe', paper, 'user-1')).rejects.toMatchObject({
      code: 'GRAPH_API_INVALID_RESPONSE',
      status: 502,
    });
  });
});
