import { createHash, createHmac } from 'node:crypto';
import { afterEach, describe, expect, it, vi } from 'vitest';
import fixture from '../services/graph-api/tests/fixtures/service-request-v1.json';
import { signedGraphHeaders } from '../lib/server/graph-service-auth';
import { resolveGraphApiConfiguration } from '../lib/server/paper-import';

const config = { baseUrl: new URL('https://graph.example'), serviceToken: fixture.key };
afterEach(() => vi.restoreAllMocks());

describe('request-bound server service attestation', () => {
  it('matches the Python golden vector without exposing the signing key', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(fixture.claims.iat * 1000);
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(fixture.claims.jti as `${string}-${string}-${string}-${string}-${string}`);
    const headers = await signedGraphHeaders(config, new URL(fixture.claims.target, config.baseUrl),
      'POST', fixture.body, fixture.claims.actor_id, fixture.claims.workspace_id);
    expect(headers.get('authorization')).toBe('Bearer ' + fixture.token);
    expect(headers.get('authorization')).not.toContain(fixture.key);
  });

  it('binds the query, exact UTF-8 body, actor, workspace and retry key and issues fresh nonces', async () => {
    const url = new URL('/v1/research/lineage?limit=10&kind=%C3%A1', config.baseUrl);
    const body = JSON.stringify({ description: 'Toán học' });
    const issue = () => signedGraphHeaders(config, url, 'POST', body, 'user-1', fixture.claims.workspace_id,
      { 'x-idempotency-key': 'same-business-operation', 'x-fgl-actor-role': 'admin' });
    const a = await issue(), b = await issue();
    const [, encoded, signature] = a.get('authorization')!.slice(7).split('.');
    const claims = JSON.parse(Buffer.from(encoded, 'base64url').toString('utf8'));
    expect(claims).toMatchObject({ service_role: 'research_write', target: url.pathname + url.search,
      actor_id: 'user-1', actor_role: 'researcher', workspace_id: fixture.claims.workspace_id,
      body_sha256: createHash('sha256').update(body).digest('hex'), idempotency_key: 'same-business-operation' });
    expect(signature).toBe(createHmac('sha256', fixture.key).update('fgl-service.v1\0' + encoded).digest('base64url'));
    expect(a.get('authorization')).not.toBe(b.get('authorization'));
    expect(a.get('x-fgl-actor-role')).toBe('researcher');
    expect(claims.exp - claims.iat).toBe(60);
  });

  it.each([
    ['POST', '/v1/search', 'graph_read'], ['POST', '/v1/graphs/snapshot', 'graph_read'],
    ['POST', '/v1/imports', 'graph_write'], ['POST', '/v1/contract-reviews', 'graph_write'],
    ['GET', '/v1/research/problems', 'research_read'], ['POST', '/v1/research/problems', 'research_write'],
  ] as const)('issues only %s %s with role %s', async (method, path, role) => {
    const headers = await signedGraphHeaders(config, new URL(path, config.baseUrl), method,
      method === 'POST' ? '{}' : undefined, 'user-1', fixture.claims.workspace_id);
    const encoded = headers.get('authorization')!.split('.')[1];
    expect(JSON.parse(Buffer.from(encoded, 'base64url').toString()).service_role).toBe(role);
  });

  it('rejects unknown endpoints, foreign origin, ambiguous keys and invalid credentials', async () => {
    for (const path of ['/v1/worker/run', 'https://evil.example/v1/search']) {
      await expect(signedGraphHeaders(config, new URL(path, config.baseUrl), 'POST', '{}',
        'user-1', fixture.claims.workspace_id)).rejects.toThrow();
    }
    await expect(signedGraphHeaders(config, new URL('/v1/search', config.baseUrl), 'POST', '{}',
      'user-1', fixture.claims.workspace_id, { 'idempotency-key': 'one', 'x-idempotency-key': 'two' })).rejects.toThrow();
    for (const key of ['short', ' '.repeat(32), 'é'.repeat(32)]) {
      expect(resolveGraphApiConfiguration({ GRAPH_API_URL: 'https://graph.example',
        GRAPH_API_SERVICE_TOKEN: key }, true)).toBeNull();
    }
  });
});
