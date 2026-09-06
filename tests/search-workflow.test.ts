import { describe, expect, it, vi } from 'vitest';
import {
  D1WorkspaceAccess,
  HttpGraphSearchClient,
  parseEvidenceSearchInput,
} from '../lib/server/evidence-search';

const searchResponse = {
  hits: [
    {
      uuid: '11111111-1111-4111-8111-111111111111',
      kind: 'Equation',
      logical_id: 'S3.E1',
      paper_id: '1706.03762',
      version: 7,
      valid_at: '2023-08-02T00:00:00Z',
      verification_status: 'reported',
      payload: { latex: 'Attention(Q,K,V)' },
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

describe('search request policy', () => {
  it('normalizes a bounded filter request without accepting a workspace id', () => {
    expect(
      parseEvidenceSearchInput({
        query: '  scaled   attention  ',
        paper_id: '1706.03762',
        version: 7,
        entity_types: ['Equation'],
        verification_statuses: ['reported'],
        as_of: '2023-08-02T00:00:00Z',
        limit: 10,
      }),
    ).toEqual({
      query: 'scaled attention',
      paper_id: '1706.03762',
      version: 7,
      entity_types: ['Equation'],
      verification_statuses: ['reported'],
      as_of: '2023-08-02T00:00:00Z',
      limit: 10,
    });
    expect(() =>
      parseEvidenceSearchInput({ query: 'attention', workspace_id: 'attacker' }),
    ).toThrow('Unknown search field');
  });

  it('rejects duplicate enums, naive timestamps and invalid centers', () => {
    expect(() =>
      parseEvidenceSearchInput({
        query: 'attention',
        entity_types: ['Equation', 'Equation'],
      }),
    ).toThrow('Duplicate');
    expect(() =>
      parseEvidenceSearchInput({
        query: 'attention',
        as_of: '2024-02-14T10:00:00',
      }),
    ).toThrow('timezone');
    expect(() =>
      parseEvidenceSearchInput({
        query: 'attention',
        center_node_uuid: 'not-a-uuid',
      }),
    ).toThrow('center');
  });
});

describe('Graph API search client', () => {
  it('adds only the server-derived tenant and never follows redirects', async () => {
    let upstreamBody: Record<string, unknown> | undefined;
    const fetchImplementation = vi.fn(async (_input, init) => {
      upstreamBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return Response.json(searchResponse);
    }) as unknown as typeof fetch;
    const client = new HttpGraphSearchClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-secret',
      },
      fetchImplementation,
    );

    const result = await client.search(
      { query: 'scaled attention', entity_types: ['Equation'] },
      'ws_server_derived',
    );

    expect(upstreamBody).toMatchObject({
      query: 'scaled attention',
      workspace_id: 'ws_server_derived',
    });
    expect(fetchImplementation).toHaveBeenCalledWith(
      new URL('https://graph.example/v1/search'),
      expect.objectContaining({ redirect: 'manual' }),
    );
    expect(result.hits[0].payload.latex).toBe('Attention(Q,K,V)');
  });

  it('rejects malformed upstream rankings', async () => {
    const malformed = structuredClone(searchResponse);
    malformed.hits[0].score = 0;
    const client = new HttpGraphSearchClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-secret',
      },
      vi.fn(async () => Response.json(malformed)) as unknown as typeof fetch,
    );

    await expect(
      client.search({ query: 'attention' }, 'ws_safe'),
    ).rejects.toMatchObject({
      code: 'GRAPH_API_INVALID_RESPONSE',
      status: 502,
    });
  });
});

describe('D1 search ownership guard', () => {
  it('checks both the workspace and signed-in owner', async () => {
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
            return { id: 'ws_owned' };
          },
        };
      },
    };
    const access = new D1WorkspaceAccess(database as unknown as D1Database);

    await expect(access.isOwned('ws_owned', 'user-a')).resolves.toBe(true);
    expect(sql).toContain('id = ? AND owner_user_id = ?');
    expect(values).toEqual(['ws_owned', 'user-a']);
  });
});
