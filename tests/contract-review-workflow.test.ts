import { describe, expect, it, vi } from 'vitest';
import {
  HttpContractReviewClient,
  parseContractReviewInput,
} from '../lib/server/contract-review';

const equationUuid = '22222222-2222-4222-8222-222222222222';
const reviewUuid = '33333333-3333-4333-8333-333333333333';
const input = {
  equation_uuid: equationUuid,
  symbol_name: 'x',
  decision: 'accepted' as const,
  reviewed_contract: {
    name: 'x',
    category: 'scalar',
    shape: [],
    domain: 'real',
    constraints: [],
  },
  evidence: ['source-anchor:S1.E1'],
};

describe('contract review boundary', () => {
  it('preserves function input and reviewed output separately and fails closed without codomain', () => {
    const contract = { ...input.reviewed_contract, category: 'function', shape: [64],
      feature_rank: 16, feature_output_domain: 'strictly_positive_real' };
    const parsed = parseContractReviewInput({ ...input, reviewed_contract: contract });
    expect(parsed.reviewed_contract).toMatchObject(contract);
    for (const changed of [{ feature_output_domain: null }, { shape: null },
      { shape: ['unknown'] }, { domain: 'positive' }, { category: 'vector' }]) {
      expect(() => parseContractReviewInput({ ...input,
        reviewed_contract: { ...contract, ...changed } })).toThrow();
    }
  });
  it('preserves explicit port metadata and accepts a nullable legacy scope', () => {
    const result = parseContractReviewInput({
      ...input,
      reviewed_contract: {
        ...input.reviewed_contract,
        scope: null,
        category: 'vector',
        feature_rank: 128,
        normalization: 'none',
        mask: 'none',
        causal: false,
        resource_class: 'not_applicable',
      },
    });
    expect(result.reviewed_contract).toMatchObject({
      normalization: 'none', mask: 'none', causal: false,
      resource_class: 'not_applicable', feature_rank: 128,
    });
  });

  it.each([
    { normalization: 'approved-by-ai' },
    { mask: 'invented' },
    { causal: 'false' },
    { resource_class: 'unreviewed-gpu' },
    { feature_rank: true, category: 'vector' },
    { feature_rank: 12, category: 'scalar' },
  ])('rejects invalid port metadata %j', (metadata) => {
    expect(() => parseContractReviewInput({
      ...input,
      reviewed_contract: { ...input.reviewed_contract, ...metadata },
    })).toThrow();
  });

  it('rejects browser-supplied approval and reviewer identity', () => {
    expect(() =>
      parseContractReviewInput({
        ...input,
        approved: true,
        reviewer_id: 'model-user',
      }),
    ).toThrow(/Unknown field/);
  });

  it('derives workspace and actor outside the request body', async () => {
    let upstreamBody: Record<string, unknown> | undefined;
    let upstreamHeaders: Headers | undefined;
    const fetchImplementation = vi.fn(async (_request, init) => {
      upstreamBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
      upstreamHeaders = new Headers(init?.headers);
      return Response.json({
        ...input,
        review_id: reviewUuid,
        workspace_id: 'ws_server_derived',
        reviewer_id: 'authenticated-user',
        reviewer_role: 'researcher',
        schema_version: 'contract-review.v1',
        reviewed_at: '2026-09-09T05:00:00Z',
        replayed: false,
      });
    }) as unknown as typeof fetch;
    const client = new HttpContractReviewClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-secret',
      },
      fetchImplementation,
    );

    const result = await client.append(
      input,
      'ws_server_derived',
      'authenticated-user',
      'review-idempotency-0001',
    );

    expect(upstreamBody).toEqual({
      ...input,
      workspace_id: 'ws_server_derived',
    });
    expect(upstreamBody).not.toHaveProperty('reviewer_id');
    expect(upstreamHeaders?.get('x-fgl-actor-id')).toBe('authenticated-user');
    expect(upstreamHeaders?.get('x-fgl-actor-role')).toBe('researcher');
    expect(upstreamHeaders?.get('idempotency-key')).toBe(
      'review-idempotency-0001',
    );
    expect(result.review_id).toBe(reviewUuid);
    expect(result.reviewer_id).toBe('authenticated-user');
  });

  it('never follows a redirect carrying the service identity', async () => {
    const fetchImplementation = vi.fn(async () =>
      Response.redirect('https://attacker.example', 307),
    ) as unknown as typeof fetch;
    const client = new HttpContractReviewClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-secret',
      },
      fetchImplementation,
    );

    await expect(
      client.append(
        input,
        'ws_server_derived',
        'authenticated-user',
        'review-idempotency-0002',
      ),
    ).rejects.toMatchObject({ code: 'GRAPH_API_UNAVAILABLE' });
    expect(fetchImplementation).toHaveBeenCalledWith(
      new URL('https://graph.example/v1/contract-reviews'),
      expect.objectContaining({ redirect: 'manual' }),
    );
  });

  it('replayed response preserves original timestamp', async () => {
    const originalTimestamp = '2026-09-09T05:00:00Z';
    const replayResponse = {
      ...input,
      review_id: reviewUuid,
      workspace_id: 'ws_server_derived',
      reviewer_id: 'authenticated-user',
      reviewer_role: 'researcher',
      schema_version: 'contract-review.v1',
      reviewed_at: originalTimestamp,
      replayed: true,
    };
    const fetchImplementation = vi.fn(
      async () => Response.json(replayResponse),
    ) as unknown as typeof fetch;
    const client = new HttpContractReviewClient(
      {
        baseUrl: new URL('https://graph.example'),
        serviceToken: 'service-secret',
      },
      fetchImplementation,
    );

    const first = await client.append(
      input,
      'ws_server_derived',
      'authenticated-user',
      'review-idempotency-0001',
    );
    const second = await client.append(
      input,
      'ws_server_derived',
      'authenticated-user',
      'review-idempotency-0001',
    );

    expect(first.replayed).toBe(true);
    expect(second.replayed).toBe(true);
    expect(first.reviewed_at).toBe(originalTimestamp);
    expect(second.reviewed_at).toBe(originalTimestamp);
  });
});
