import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ user: vi.fn(), first: vi.fn() }));
vi.mock('@/app/chatgpt-auth', () => ({ getChatGPTUser: mocks.user }));
vi.mock('@/db', () => ({
  getD1: () => ({
    prepare: () => ({
      bind: () => ({ first: mocks.first }),
    }),
  }),
}));
vi.mock('cloudflare:workers', () => ({
  env: {
    APP_ENV: 'development',
    GRAPH_API_URL: 'http://localhost:8000',
    GRAPH_API_SERVICE_TOKEN: 'development-test-key-with-more-than-32-bytes',
  },
}));

import { POST } from '../app/api/checks/symbolic/route';

const VALID_UUID = '11111111-1111-4111-8111-111111111111';

function submit(body: unknown, { origin = 'https://lab.test', key = 'idem-key-0123456789' } = {}) {
  return POST(
    new Request('https://lab.test/api/checks/symbolic', {
      method: 'POST',
      headers: {
        origin,
        'content-type': 'application/json',
        'idempotency-key': key,
      },
      body: JSON.stringify(body),
    }),
  );
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

it('rejects unauthenticated, cross-origin, spoofed, and malformed chain requests fail-closed', async () => {
  mocks.user.mockResolvedValue(null);
  expect((await submit({ target_uuid: VALID_UUID, formula_b: 'a' })).status).toBe(401);

  mocks.user.mockResolvedValue({ userId: 'user-1' });
  expect((await submit({ target_uuid: VALID_UUID, formula_b: 'a' }, { origin: 'https://evil.test' })).status).toBe(403);

  // Missing both formula_b and steps
  expect((await submit({ target_uuid: VALID_UUID })).status).toBe(400);
  // Spoofed formula_a rejected by allowlist
  expect((await submit({ target_uuid: VALID_UUID, formula_a: 'a', formula_b: 'a' })).status).toBe(400);
  // > 8 derivation steps rejected
  expect((await submit({ target_uuid: VALID_UUID, steps: Array(9).fill('a') })).status).toBe(400);
  // Mismatched formula_b and steps[-1]
  expect((await submit({ target_uuid: VALID_UUID, formula_b: 'b', steps: ['a'] })).status).toBe(400);
  // Non-boolean include_lean_export
  expect((await submit({ target_uuid: VALID_UUID, steps: ['a'], include_lean_export: 'yes' })).status).toBe(400);
});

it('signs and forwards derivation chain + include_lean_export to /v1/checks/symbolic/quick', async () => {
  mocks.user.mockResolvedValue({ userId: 'user-1' });
  mocks.first.mockResolvedValue({ id: 'ws-owned' });
  const fetcher = vi.fn(async (url: URL, init: RequestInit) => {
    expect(url.pathname).toBe('/v1/checks/symbolic/quick');
    expect(new Headers(init.headers).get('authorization')).toMatch(/^Bearer fgl1\./);
    const parsed = JSON.parse(typeof init.body === 'string' ? init.body : '');
    expect(parsed.target_uuid).toBe(VALID_UUID);
    expect(parsed.steps).toEqual(['x + (a + b) - b', 'x + a']);
    expect(parsed.include_lean_export).toBe(true);
    expect(typeof parsed.workspace_id).toBe('string');
    return Response.json({
      outcome: 'supported',
      scope: 'ad_hoc_check_not_frozen_campaign',
      steps: [],
    });
  });
  vi.stubGlobal('fetch', fetcher);

  const response = await submit({
    target_uuid: VALID_UUID,
    steps: ['x + (a + b) - b', 'x + a'],
    include_lean_export: true,
  });
  expect(response.status).toBe(200);
  expect(fetcher).toHaveBeenCalledOnce();
});
