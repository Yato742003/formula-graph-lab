import assert from 'node:assert/strict';

// Run against an already-started production build, never the Vite HMR server.
const base = new URL(process.argv[2] ?? 'http://127.0.0.1:4173');
const policies = [];
for (let i = 0; i < 2; i++) {
  const response = await fetch(new URL('/__fgl_security_not_found__', base), {
    redirect: 'manual', headers: {
      'x-nonce': 'attacker',
      'content-security-policy': "script-src 'unsafe-inline'",
    },
  });
  assert.equal(response.status, 404);
  const policy = response.headers.get('content-security-policy');
  const nonce = policy?.match(/nonce-([a-f0-9]{32})/)?.[1];
  assert.ok(nonce, 'Production CSP must authorize SSR scripts with a fresh nonce');
  const html = await response.text();
  const scripts = [...html.matchAll(/<script\b[^>]*>/g)].map((match) => match[0]);
  assert.ok(scripts.length > 0);
  assert.ok(scripts.every((script) => script.includes(`nonce="${nonce}"`)));
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(response.headers.get('cache-control'), 'no-store');
  policies.push(policy);
}
assert.notEqual(...policies);
const api = await fetch(new URL('/api/graph', base), { redirect: 'manual' });
assert.equal(api.status, 401, 'Unauthenticated BFF must remain denied');
console.log('PASS: production nonce propagation, no-store, nosniff and anonymous BFF denial');
console.log('Scope: HTTP/SSR only; not authenticated browser or production ingress acceptance.');
