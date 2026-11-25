import {expect, it, vi} from 'vitest';
vi.mock('@/app/chatgpt-auth', () => ({getChatGPTUser: () => ({userId:'researcher'})}));
vi.mock('@/db', () => ({getD1: vi.fn()}));
vi.mock('cloudflare:workers', () => ({env:{}}));
import {GET, POST} from '../app/api/contract-reviews/batch/route';

it('rejects workspace overrides, cross-origin writes and missing idempotency keys', async () => {
  expect((await GET(new Request('https://lab.test/?equation_uuid=a&workspace_id=b'))).status).toBe(400);
  expect((await POST(new Request('https://lab.test', {method:'POST', headers:{origin:'https://other.test'}}))).status).toBe(403);
  expect((await POST(new Request('https://lab.test', {method:'POST', headers:{'content-type':'application/json'}}))).status).toBe(400);
});
