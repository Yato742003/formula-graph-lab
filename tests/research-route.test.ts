import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ owned: vi.fn(), user: vi.fn() }));
vi.mock('@/app/chatgpt-auth', () => ({ getChatGPTUser: mocks.user }));
vi.mock('@/db', () => ({ getD1: () => ({}) }));
vi.mock('@/lib/server/evidence-search', () => ({
  D1WorkspaceAccess: class {
    isOwned = mocks.owned;
  },
}));
vi.mock('cloudflare:workers', () => ({
  env: {
    APP_ENV: 'development',
    GRAPH_API_URL: 'http://localhost:8000',
    GRAPH_API_SERVICE_TOKEN: 'service-test-secret-with-at-least-32-chars',
  },
}));
import { GET, POST } from '../app/api/research/[...slug]/route';
import { workspaceIdentifierForUser } from '../lib/server/paper-import';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});
const context = (slug: string[]) => ({ params: Promise.resolve({ slug }) });

it('a production build refuses loopback HTTP even when APP_ENV says development', async () => {
  vi.stubEnv('DEV', false);
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  const response = await GET(new Request('https://app.test/api/research/problems'), context(['problems']));
  expect(response.status).toBe(503);
  expect(fetcher).not.toHaveBeenCalled();
});

it('signs the authenticated owner, not browser identity, role or workspace headers', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json({ items: [] }));
  vi.stubGlobal('fetch', fetcher);
  const response = await GET(new Request('https://app.test/api/research/problems', { headers: {
    authorization: 'Bearer browser-token', 'x-fgl-actor-id': 'other-user',
    'x-fgl-actor-role': 'admin', 'x-fgl-workspace-id': 'foreign-workspace',
  } }), context(['problems']));
  expect(response.status).toBe(200);
  const workspace = await workspaceIdentifierForUser('user-1');
  expect(mocks.owned).toHaveBeenCalledWith(workspace, 'user-1');
  const headers = new Headers(fetcher.mock.calls[0][1]?.headers);
  const encoded = headers.get('authorization')!.split('.')[1];
  expect(JSON.parse(Buffer.from(encoded, 'base64url').toString())).toMatchObject({
    actor_id: 'user-1', actor_role: 'researcher', workspace_id: workspace,
    service_role: 'research_read', method: 'GET', target: '/v1/research/problems',
  });
  expect(headers.get('authorization')).not.toContain('browser-token');
});

it('allows bounded evolution commands but never browser metrics or holdout overrides', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json({ campaign: {}, replayed: false }));
  vi.stubGlobal('fetch', fetcher);
  const eid = `evo_${'a'.repeat(32)}`;
  const write = (action: string, body: unknown) => POST(new Request(`https://app.test/api/research/evolution/${eid}/${action}`, {
    method: 'POST', headers: { 'content-type': 'application/json', 'x-idempotency-key': 'bounded-step' },
    body: JSON.stringify(body),
  }), context(['evolution', eid, action]));
  expect((await write('generation', { metrics: [], fitness: 1 })).status).toBe(400);
  expect((await write('confirm', { evaluation_role: 'search', seed: 1 })).status).toBe(400);
  expect(fetcher).not.toHaveBeenCalled();
  expect((await write('generation', { seed_candidate_id: `cand_${'b'.repeat(32)}` })).status).toBe(200);
  expect((await write('confirm', {})).status).toBe(200);
  const headers = new Headers(fetcher.mock.calls[0]?.[1]?.headers);
  expect(headers.get('x-fgl-actor-role')).toBe('researcher');
});

it('rejects unauthenticated access before upstream fetch', async () => {
  mocks.user.mockResolvedValue(null);
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  expect(
    (
      await GET(
        new Request('https://app.test/api/research/problems'),
        context(['problems']),
      )
    ).status,
  ).toBe(401);
  expect(fetcher).not.toHaveBeenCalled();
});

it('rejects cross-site writes, path traversal, and workspace query spoofing', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  expect(
    (
      await POST(
        new Request('https://app.test/api/research/problems', {
          method: 'POST',
          headers: { origin: 'https://evil.test' },
        }),
        context(['problems']),
      )
    ).status,
  ).toBe(403);
  expect(
    (
      await GET(
        new Request('https://app.test/api/research/problems'),
        context(['..', 'health']),
      )
    ).status,
  ).toBe(404);
  expect(
    (
      await GET(
        new Request(
          'https://app.test/api/research/problems?workspace_id=other',
        ),
        context(['problems']),
      )
    ).status,
  ).toBe(400);
  expect(fetcher).not.toHaveBeenCalled();
});

it('requires ownership and bounds upstream payloads', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(false);
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  const request = new Request('https://app.test/api/research/problems');
  expect((await GET(request, context(['problems']))).status).toBe(404);
  expect(fetcher).not.toHaveBeenCalled();
  mocks.owned.mockResolvedValue(true);
  fetcher.mockResolvedValue(
    new Response('{}', { headers: { 'content-length': '5000000' } }),
  );
  const response = await GET(request, context(['problems']));
  expect(response.status).toBe(502);
  expect(await response.json()).toEqual({
    code: 'UPSTREAM_RESPONSE_TOO_LARGE',
  });
  expect(fetcher.mock.calls[0][1].redirect).toBe('manual');
});

it('forwards metadata observations but rejects browser claims of imported HTML', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ paper_id: 'paper-c', has_html: false }),
  );
  vi.stubGlobal('fetch', fetcher);
  const payload = {
    paper_id: 'paper-c',
    title: 'Missing HTML',
    source_reference: 'Bibliography',
  };
  const request = (extra = {}) =>
    new Request('https://app.test/api/research/lineage/coverage', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'metadata-key',
      },
      body: JSON.stringify({ ...payload, ...extra }),
    });
  expect(
    (await POST(request({ has_html: true }), context(['lineage', 'coverage'])))
      .status,
  ).toBe(400);
  expect(fetcher).not.toHaveBeenCalled();
  expect((await POST(request(), context(['lineage', 'coverage']))).status).toBe(
    200,
  );
  expect(fetcher).toHaveBeenCalledOnce();
});

it('forwards a scoped source lookup without accepting workspace spoofing', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ items: [] }),
  );
  vi.stubGlobal('fetch', fetcher);
  const slug = context(['lineage', 'sources']);
  const request = new Request(
    'https://app.test/api/research/lineage/sources?source_id=source-1',
  );
  expect((await GET(request, slug)).status).toBe(200);
  expect(
    (fetcher.mock.calls[0][0] as URL).pathname +
      (fetcher.mock.calls[0][0] as URL).search,
  ).toBe('/v1/research/lineage/sources?source_id=source-1');
  expect(
    (await GET(new Request(`${request.url}&workspace_id=foreign`), slug))
      .status,
  ).toBe(400);
  expect(fetcher).toHaveBeenCalledOnce();
});

it('forwards bounded saved-candidate history reads with only approved query fields', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ items: [], total: 0 }),
  );
  vi.stubGlobal('fetch', fetcher);
  const slug = context(['candidates']);
  const request = new Request(
    'https://app.test/api/research/candidates?limit=20&offset=40',
  );
  expect((await GET(request, slug)).status).toBe(200);
  const upstream = fetcher.mock.calls[0]?.[0] as URL;
  expect(upstream.pathname + upstream.search).toBe(
    '/v1/research/candidates?limit=20&offset=40',
  );
  expect(
    (
      await GET(
        new Request('https://app.test/api/research/candidates?workspace_id=other'),
        slug,
      )
    ).status,
  ).toBe(400);
  expect(
    (
      await GET(
        new Request('https://app.test/api/research/candidates?limit=10&limit=20'),
        slug,
      )
    ).status,
  ).toBe(400);
  expect(fetcher).toHaveBeenCalledOnce();
});

it('forwards only scoped evolution history and human control fields', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ campaign: { status: 'active' } }, { status: 201 }),
  );
  vi.stubGlobal('fetch', fetcher);
  const evolutionId = `evo_${'a'.repeat(32)}`;
  const start = await POST(
    new Request('https://app.test/api/research/evolution', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'evolution-start',
      },
      body: JSON.stringify({ spec_id: 'spec-1' }),
    }),
    context(['evolution']),
  );
  expect(start.status).toBe(201);
  const startUpstream = fetcher.mock.calls[0]?.[0];
  expect(startUpstream).toBeInstanceOf(URL);
  if (!(startUpstream instanceof URL)) throw new Error('Expected the API URL');
  expect(startUpstream.pathname).toBe('/v1/research/evolution');

  const history = await GET(
    new Request('https://app.test/api/research/evolution?limit=10&offset=0'),
    context(['evolution']),
  );
  expect(history.status).toBe(201);
  const historyUpstream = fetcher.mock.calls[1]?.[0];
  expect(historyUpstream).toBeInstanceOf(URL);
  if (!(historyUpstream instanceof URL)) throw new Error('Expected the API URL');
  expect(historyUpstream.pathname).toBe('/v1/research/evolution');

  const finalists = await POST(
    new Request(`https://app.test/api/research/evolution/${evolutionId}/finalists`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'evolution-finalists',
      },
      body: JSON.stringify({ finalist_ids: [`cand_${'b'.repeat(32)}`] }),
    }),
    context(['evolution', evolutionId, 'finalists']),
  );
  expect(finalists.status).toBe(201);

  const forged = await POST(
    new Request(`https://app.test/api/research/evolution/${evolutionId}/finalists`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'evolution-forged',
      },
      body: JSON.stringify({
        finalist_ids: [`cand_${'b'.repeat(32)}`],
        metrics: [{ name: 'quality', value: 1 }],
      }),
    }),
    context(['evolution', evolutionId, 'finalists']),
  );
  expect(forged.status).toBe(400);
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it('exposes proposal history as read-only workspace data', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ items: [], total: 0 }),
  );
  vi.stubGlobal('fetch', fetcher);
  const request = new Request(
    'https://app.test/api/research/proposals?limit=10&offset=20',
  );
  expect((await GET(request, context(['proposals']))).status).toBe(200);
  const upstream = fetcher.mock.calls[0]?.[0];
  expect(upstream).toBeInstanceOf(URL);
  if (!(upstream instanceof URL)) throw new Error('Expected the API URL');
  expect(upstream.pathname + upstream.search).toBe(
    '/v1/research/proposals?limit=10&offset=20',
  );
  expect(
    (
      await POST(
        new Request('https://app.test/api/research/proposals', {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            'x-idempotency-key': 'not-a-worker-key',
          },
          body: JSON.stringify({ output_json: '{}' }),
        }),
        context(['proposals']),
      )
    ).status,
  ).toBe(404);
  expect(fetcher).toHaveBeenCalledOnce();
});

it('exposes proposal-generation readiness as a read-only workspace route', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ state: 'configured', model: 'test-model', max_generation_cost_usd: '0.02', daily_max_cost_usd: '0.10' }),
  );
  vi.stubGlobal('fetch', fetcher);
  const request = new Request(
    'https://app.test/api/research/proposals/capabilities',
  );
  expect(
    (await GET(request, context(['proposals', 'capabilities']))).status,
  ).toBe(200);
  const upstream = fetcher.mock.calls[0]?.[0];
  expect(upstream).toBeInstanceOf(URL);
  if (!(upstream instanceof URL)) throw new Error('Expected the API URL');
  expect(upstream.pathname).toBe('/v1/research/proposals/capabilities');
  expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get('x-fgl-workspace-id'))
    .toMatch(/^ws_[a-f0-9]{48}$/);
  expect(
    (await POST(
      new Request(request.url, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-idempotency-key': 'not-allowed' },
        body: '{}',
      }),
      context(['proposals', 'capabilities']),
    )).status,
  ).toBe(404);
  expect(fetcher).toHaveBeenCalledOnce();
});

it('forwards only the bounded human proposal-generation payload', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ proposal: { review_state: 'pending' } }, { status: 201 }),
  );
  vi.stubGlobal('fetch', fetcher);
  const input = {
    spec_id: 'spec-1',
    parent_ids: ['eq-a', 'eq-b'],
    source_span_ids: ['span_a', 'span_b'],
    research_question: 'Try a source-backed hypothesis',
  };
  const response = await POST(
    new Request('https://app.test/api/research/proposals/generate', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'generation-key',
      },
      body: JSON.stringify(input),
    }),
    context(['proposals', 'generate']),
  );
  expect(response.status).toBe(201);
  const [upstream, init] = fetcher.mock.calls[0] ?? [];
  expect(upstream).toBeInstanceOf(URL);
  if (!(upstream instanceof URL)) throw new Error('Expected the API URL');
  expect(upstream.pathname).toBe('/v1/research/proposals/generate');
  if (typeof init?.body !== 'string') throw new Error('Expected forwarded JSON body');
  expect(JSON.parse(init.body)).toEqual(input);
  expect(new Headers(init?.headers).get('x-fgl-workspace-id')).toMatch(/^ws_[a-f0-9]{48}$/);
  expect(init?.signal).toBeDefined();

  const invalid = await POST(
    new Request('https://app.test/api/research/proposals/generate', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'generation-key-extra',
      },
      body: JSON.stringify({ ...input, approved: true }),
    }),
    context(['proposals', 'generate']),
  );
  expect(invalid.status).toBe(400);
  expect(fetcher).toHaveBeenCalledOnce();
});

it('forwards only human proposal review writes to the scoped API route', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ review: { decision: 'reject' } }, { status: 201 }),
  );
  vi.stubGlobal('fetch', fetcher);
  const request = new Request(
    `https://app.test/api/research/proposals/prop_${'a'.repeat(32)}/reviews`,
    {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'review-key',
      },
      body: JSON.stringify({ decision: 'reject', notes: 'Not supported.' }),
    },
  );
  expect(
    (await POST(request, context(['proposals', `prop_${'a'.repeat(32)}`, 'reviews'])))
      .status,
  ).toBe(201);
  const [upstream, init] = fetcher.mock.calls[0] ?? [];
  expect(upstream).toBeInstanceOf(URL);
  if (!(upstream instanceof URL)) throw new Error('Expected the API URL');
  expect(upstream.pathname).toBe(
    `/v1/research/proposals/prop_${'a'.repeat(32)}/reviews`,
  );
  expect(new Headers(init?.headers).get('x-fgl-actor-id')).toBe('user-1');
  expect(new Headers(init?.headers).get('x-fgl-actor-role')).toBe('researcher');

  const forged = await POST(
    new Request(request.url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'review-key-2',
      },
      body: JSON.stringify({ decision: 'accept_for_compilation', notes: 'OK', reviewer_id: 'admin' }),
    }),
    context(['proposals', `prop_${'a'.repeat(32)}`, 'reviews']),
  );
  expect(forged.status).toBe(400);
  expect(fetcher).toHaveBeenCalledOnce();
});

it('compiles only strict candidate requests and derives workspace from the signed-in user', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      Response.json(
        { candidate: { candidate_id: 'cand_test' } },
        { status: 201 },
      ),
  );
  vi.stubGlobal('fetch', fetcher);
  const payload = {
    spec_id: 'spec-1',
    mapping_id: 'mapping-1',
    transform: {
      operator: 'mix_positive_feature_maps',
      operator_version: '1',
      target_node_id: 'eq-1',
      parameters: { lambda: 0.25 },
      bindings: { left: 'sym-1', right: 'sym-2' },
    },
  };
  const request = (extra = {}) =>
    new Request('https://app.test/api/research/candidates/compile', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'compile-candidate-key',
      },
      body: JSON.stringify({ ...payload, ...extra }),
    });
  expect(
    (await POST(request(), context(['candidates', 'compile']))).status,
  ).toBe(201);
  const call = fetcher.mock.calls[0];
  expect(call).toBeDefined();
  const upstreamBody = JSON.parse(call?.[1]?.body as string);
  expect(upstreamBody.workspace_id).toMatch(/^ws_/);
  expect(upstreamBody.spec_id).toBe('spec-1');
  expect(new Headers(call?.[1]?.headers).get('x-fgl-actor-id')).toBe('user-1');
  expect(
    (
      await POST(
        request({ proposal_id: `prop_${'a'.repeat(32)}` }),
        context(['candidates', 'compile']),
      )
    ).status,
  ).toBe(201);
  const proposalCall = fetcher.mock.calls[1];
  expect(JSON.parse(proposalCall?.[1]?.body as string).proposal_id).toBe(
    `prop_${'a'.repeat(32)}`,
  );
  expect(
    (
      await POST(
        request({ workspace_id: 'foreign' }),
        context(['candidates', 'compile']),
      )
    ).status,
  ).toBe(400);
  expect(
    (
      await GET(
        new Request('https://app.test/api/research/candidates/compile'),
        context(['candidates', 'compile']),
      )
    ).status,
  ).toBe(404);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('accepts only an admission request and forwards no client-supplied evidence', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ decision: { allowed: false } }, { status: 201 }),
  );
  vi.stubGlobal('fetch', fetcher);
  const slug = context(['candidates', 'cand_' + 'a'.repeat(32), 'admission']);
  const request = (payload: Record<string, unknown>) =>
    new Request('https://app.test/api/research/candidates/candidate/admission', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'admission-key-1',
      },
      body: JSON.stringify(payload),
    });
  expect(
    (await POST(request({ action: 'can_run_numerical' }), slug)).status,
  ).toBe(201);
  expect(
    (await POST(request({ action: 'can_run_numerical', verification: { symbolic: 'supported' } }), slug))
      .status,
  ).toBe(400);
  const call = fetcher.mock.calls[0];
  if (!call) throw new Error('Admission route was not forwarded.');
  expect((call[0] as URL).pathname).toBe(
    '/v1/research/candidates/cand_' + 'a'.repeat(32) + '/admission',
  );
  expect(JSON.parse(call[1]?.body as string)).toEqual({
    action: 'can_run_numerical',
  });
  expect(fetcher).toHaveBeenCalledOnce();
});

it('forwards empty candidate-check requests without allowing client verdicts', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ check: { outcome: 'unknown' } }, { status: 201 }),
  );
  vi.stubGlobal('fetch', fetcher);
  const slug = context(['candidates', 'cand_' + 'b'.repeat(32), 'verify']);
  const request = (payload: Record<string, unknown>) =>
    new Request('https://app.test/api/research/candidates/verify', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'candidate-check-key-1',
      },
      body: JSON.stringify(payload),
    });
  expect((await POST(request({}), slug)).status).toBe(201);
  expect((await POST(request({ outcome: 'supported' }), slug)).status).toBe(400);
  const call = fetcher.mock.calls[0];
  if (!call) throw new Error('Candidate check route was not forwarded.');
  expect((call[0] as URL).pathname).toBe(
    '/v1/research/candidates/cand_' + 'b'.repeat(32) + '/verify',
  );
  expect(JSON.parse(call[1]?.body as string)).toEqual({});
  expect(fetcher).toHaveBeenCalledOnce();
});

it('forwards only a synthetic fixture seed for a persisted candidate', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ result: { outcome: 'passed_suite', performance_claim: false } }, { status: 201 }),
  );
  vi.stubGlobal('fetch', fetcher);
  const slug = context(['candidates', 'cand_' + 'c'.repeat(32), 'numerical-fixture']);
  const request = (payload: Record<string, unknown>) =>
    new Request('https://app.test/api/research/candidates/numerical-fixture', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'fixture-run-key-1',
      },
      body: JSON.stringify(payload),
    });

  expect((await POST(request({ seed: 7 }), slug)).status).toBe(201);
  expect((await POST(request({ seed: 7, performance_claim: true }), slug)).status).toBe(400);
  const call = fetcher.mock.calls[0];
  if (!call) throw new Error('Numerical fixture route was not forwarded.');
  expect((call[0] as URL).pathname).toBe(
    '/v1/research/candidates/cand_' + 'c'.repeat(32) + '/numerical-fixture',
  );
  expect(JSON.parse(call[1]?.body as string)).toEqual({ seed: 7 });
  expect(fetcher).toHaveBeenCalledOnce();
});

it('forwards an empty body for the gated registered research case', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ result: { outcome: 'failed_on_protocol', performance_claim: false } }, { status: 201 }),
  );
  vi.stubGlobal('fetch', fetcher);
  const candidate = 'cand_' + 'f'.repeat(32);
  const slug = context(['candidates', candidate, 'research-case']);
  const response = await POST(
    new Request('https://app.test/api/research/candidates/research-case', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-idempotency-key': 'research-case-key-1',
      },
      body: JSON.stringify({}),
    }),
    slug,
  );
  expect(response.status).toBe(201);
  const call = fetcher.mock.calls[0];
  if (!call) throw new Error('Research-case route was not forwarded.');
  expect((call[0] as URL).pathname).toBe(
    `/v1/research/candidates/${candidate}/research-case`,
  );
  expect(JSON.parse(call[1]?.body as string)).toEqual({});
  expect(fetcher).toHaveBeenCalledOnce();
});

it('exports only an authenticated candidate activity replay bundle', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ schema_version: 'compiler-replay-bundle.v1', bundle_hash: 'a'.repeat(64) }),
  );
  vi.stubGlobal('fetch', fetcher);
  const candidate = 'cand_' + 'd'.repeat(32);
  const activity = 'act_' + 'e'.repeat(32);
  const slug = context(['candidates', candidate, 'activities', activity, 'bundle']);

  const response = await GET(
    new Request(`https://app.test/api/research/candidates/replay-bundle?bundle_hash=${'a'.repeat(64)}`),
    slug,
  );
  expect(response.status).toBe(200);
  expect(response.headers.get('cache-control')).toBe('no-store');
  const call = fetcher.mock.calls[0];
  if (!call) throw new Error('Replay bundle route was not forwarded.');
  expect((call[0] as URL).pathname).toBe(
    `/v1/research/candidates/${candidate}/activities/${activity}/bundle`,
  );
  expect((call[0] as URL).searchParams.get('bundle_hash')).toBe('a'.repeat(64));
  expect(call[1]?.method).toBe('GET');
  expect(call[1]?.body).toBeUndefined();
  expect(fetcher).toHaveBeenCalledOnce();

  const invalid = await GET(
    new Request('https://app.test/api/research/candidates/replay-bundle'),
    context(['candidates', candidate, 'activities', 'act_wrong', 'bundle']),
  );
  expect(invalid.status).toBe(404);
  const invalidHash = await GET(
    new Request(`https://app.test/api/research/candidates/replay-bundle?bundle_hash=${'z'.repeat(64)}`),
    slug,
  );
  expect(invalidHash.status).toBe(400);
  expect(fetcher).toHaveBeenCalledOnce();
});

it('exports only an authenticated candidate activity replay report', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.owned.mockResolvedValue(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json({ schema_version: 'compiler-replay-report.v1', status: 'partial' }),
  );
  vi.stubGlobal('fetch', fetcher);
  const candidate = 'cand_' + 'f'.repeat(32);
  const activity = 'act_' + 'a'.repeat(32);
  const response = await GET(
    new Request('https://app.test/api/research/candidates/replay-report'),
    context(['candidates', candidate, 'activities', activity, 'report']),
  );

  expect(response.status).toBe(200);
  expect(response.headers.get('cache-control')).toBe('no-store');
  const call = fetcher.mock.calls[0];
  if (!call) throw new Error('Replay report route was not forwarded.');
  expect((call[0] as URL).pathname).toBe(
    `/v1/research/candidates/${candidate}/activities/${activity}/report`,
  );
  expect(call[1]?.method).toBe('GET');
  expect(call[1]?.body).toBeUndefined();
  expect(fetcher).toHaveBeenCalledOnce();

  fetcher.mockClear();
  const invalid = await GET(
    new Request('https://app.test/api/research/candidates/replay-report'),
    context(['candidates', candidate, 'activities', activity, 'report', 'extra']),
  );
  expect(invalid.status).toBe(404);
  expect(fetcher).not.toHaveBeenCalled();
});
