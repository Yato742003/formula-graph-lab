import { describe, expect, it, vi } from 'vitest';
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
        serviceToken: 'service-secret',
      },
      fetchImplementation,
    );

    const result = await client.load('ws_server_derived', paper);

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
        serviceToken: 'service-secret',
      },
      vi.fn(async () => Response.json(withHistory)) as unknown as typeof fetch,
    );

    const result = await client.load('ws_safe', paper);
    expect(result.nodes.at(-1)?.version).toBe(6);
    expect(result.edges.at(-1)?.relation).toBe('supersedes');
  });

  it('rejects cross-version nodes and dangling edges from the upstream service', async () => {
    const crossVersion = structuredClone(graphResponse);
    crossVersion.nodes[1].version = 6;
    const crossVersionClient = new HttpGraphSnapshotClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-secret',
      },
      vi.fn(async () => Response.json(crossVersion)) as unknown as typeof fetch,
    );
    await expect(crossVersionClient.load('ws_safe', paper)).rejects.toMatchObject({
      code: 'GRAPH_API_INVALID_RESPONSE',
      status: 502,
    });

    const dangling = structuredClone(graphResponse);
    dangling.edges[0].target_uuid = '33333333-3333-4333-8333-333333333333';
    const danglingClient = new HttpGraphSnapshotClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-secret',
      },
      vi.fn(async () => Response.json(dangling)) as unknown as typeof fetch,
    );
    await expect(danglingClient.load('ws_safe', paper)).rejects.toMatchObject({
      code: 'GRAPH_API_INVALID_RESPONSE',
      status: 502,
    });
  });
});
