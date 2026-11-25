import { afterEach, expect, it, vi } from 'vitest';
import { createHash } from 'node:crypto';

const mocks = vi.hoisted(() => ({ user: vi.fn(), batch: vi.fn(), prepare: vi.fn() }));
vi.mock('@/app/chatgpt-auth', () => ({ getChatGPTUser: mocks.user }));
vi.mock('@/db', () => ({ getD1: () => ({ batch: mocks.batch, prepare: mocks.prepare }) }));
vi.mock('cloudflare:workers', () => ({ env: {
  APP_ENV: 'development', GRAPH_API_URL: 'http://localhost:8000',
  GRAPH_API_SERVICE_TOKEN: 'development-test-key-with-more-than-32-bytes',
} }));
import { POST } from '../app/api/imports/tex/route';

function submit(tex_source: string, origin = 'https://lab.test') {
  return POST(new Request('https://lab.test/api/imports/tex', { method: 'POST',
    headers: {origin, 'content-type': 'application/json'}, body: JSON.stringify({tex_source}) }));
}

afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals(); });

it('checks user, origin and UTF-8 size before opening a job', async () => {
  mocks.user.mockResolvedValue(null);
  expect((await submit('$$x$$')).status).toBe(401);
  mocks.user.mockResolvedValue({userId: 'user'});
  expect((await submit('$$x$$', 'https://other.test')).status).toBe(403);
  expect((await submit('é'.repeat(262145))).status).toBe(413);
  expect(mocks.prepare).not.toHaveBeenCalled();
});

it('signs local import, stores the content ID and rejects a mismatched response', async () => {
  const source = '$$x=1$$';
  const digest = createHash('sha256').update(source).digest('hex');
  const paper = { paper_id: 'tex_' + digest.slice(0,12), source_sha256: digest,
    source_kind: 'local_tex', source_url: 'urn:fgl:local-tex:' + digest, version: 1,
    title: 'Local', authors: [], version_published_at: null, metadata_warnings: [],
    equations: [], sections: [] };
  const receipt = {import_uuid: 'test', node_count: 2, edge_count: 1, episode_count: 1, replayed: false};
  const fetcher = vi.fn(async (url: URL, init: RequestInit) => {
    expect(url.pathname).toBe('/v1/evidence/import-tex');
    expect(new Headers(init.headers).get('authorization')).toMatch(/^Bearer fgl1\./);
    expect(JSON.parse(typeof init.body === "string" ? init.body : "").tex_source).toBe(source);
    return Response.json({paper, receipt});
  });
  vi.stubGlobal('fetch', fetcher);
  mocks.user.mockResolvedValue({userId: 'user'});
  const ok = {success: true, meta: {changes: 1}};
  const bind = vi.fn().mockReturnValue({first: async () => ({id:'owned'}), run: async () => ok});
  mocks.prepare.mockReturnValue({bind});
  mocks.batch.mockResolvedValue([ok, ok]);
  expect((await submit(source)).status).toBe(200);
  expect(bind.mock.calls.some(args => args.includes(paper.paper_id))).toBe(true);
  paper.source_url += 'x';
  expect((await submit(source)).status).toBe(502);
  expect(mocks.batch).toHaveBeenCalledTimes(1);
});
