import { afterEach, describe, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ env: { FGL_TRUST_SITES_IDENTITY: 'false' } }));
vi.mock('cloudflare:workers', () => ({ env: mocks.env }));
import { proxy } from '../proxy';

afterEach(() => { vi.unstubAllEnvs(); mocks.env.FGL_TRUST_SITES_IDENTITY = 'false'; });

describe('production browser security boundary', () => {
  it('generates fresh nonces, rejects caller policy and forwards the same SSR policy', () => {
    vi.stubEnv('PROD', true);
    const request = new Request('https://fgl.test/graph', { headers: {
      'x-nonce': 'attacker', 'content-security-policy': "script-src 'unsafe-inline'",
      'content-security-policy-report-only': "script-src 'unsafe-inline'",
    } });
    const first = proxy(request);
    const policy = first.headers.get('content-security-policy')!;
    expect(policy).toMatch(/script-src 'self' 'nonce-[a-f0-9]{32}' 'strict-dynamic'/);
    expect(policy.split('; ').find((p) => p.startsWith('script-src '))).not.toContain('unsafe');
    expect(first.headers.get('x-middleware-request-content-security-policy')).toBe(policy);
    expect(first.headers.get('x-middleware-request-x-nonce')).toMatch(/^[a-f0-9]{32}$/);
    expect(first.headers.get('x-middleware-request-content-security-policy-report-only')).toBeNull();
    expect(proxy(request).headers.get('content-security-policy')).not.toBe(policy);
    expect(first.headers.get('x-frame-options')).toBe('DENY');
    expect(first.headers.get('x-content-type-options')).toBe('nosniff');
    expect(first.headers.get('cache-control')).toBe('no-store');
    expect(first.headers.get('strict-transport-security')).toBe('max-age=31536000');
  });

  it('allows only the reviewed Sites parent, not arbitrary embedding domains', () => {
    vi.stubEnv('PROD', true);
    mocks.env.FGL_TRUST_SITES_IDENTITY = 'true';
    const response = proxy(new Request('https://fgl.test/guide'));
    expect(response.headers.get('content-security-policy')).toContain('frame-ancestors https://chatgpt.com');
    expect(response.headers.get('x-frame-options')).toBeNull();
  });

  it('does not label HMR as production CSP and strips spoofed nonce in development', () => {
    vi.stubEnv('PROD', false);
    const response = proxy(new Request('http://localhost:3000/graph', { headers: { 'x-nonce': 'fake' } }));
    expect(response.headers.get('content-security-policy')).toBeNull();
    expect(response.headers.get('x-middleware-request-x-nonce')).toBeNull();
    expect(response.headers.get('strict-transport-security')).toBeNull();
  });
});
