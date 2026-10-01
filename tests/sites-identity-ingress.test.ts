import { createServer, get as httpGet } from 'node:http';
import { sites } from '@openai/sites-vite-plugin';
import type { Connect, ViteDevServer } from 'vite';
import { expect, it } from 'vitest';

it('the installed Sites dev ingress strips forged headers and confines simulated login to loopback', async () => {
  let middleware!: Connect.NextHandleFunction;
  const plugin = sites();
  const configure = plugin.configureServer;
  if (typeof configure !== 'function') throw new Error('Missing Sites dev ingress.');
  await configure.call({} as never, {
    config: { server: {}, logger: { info() {} } },
    middlewares: { use(fn: Connect.NextHandleFunction) { middleware = fn; } },
  } as unknown as ViteDevServer);
  const server = createServer((request, response) => middleware(request, response, () => {
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify({ id: request.headers['oai-authenticated-user-id'] ?? null,
      raw: request.rawHeaders, cookie: request.headers.cookie ?? null }));
  }));
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Missing loopback server.');
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const anonymous = await fetch(base + '/api/research/problems', {
      headers: { 'oai-authenticated-user-id': 'forged-admin', 'oai-authenticated-user-email': 'evil@test' },
    });
    const anon = await anonymous.json() as { id: string | null; raw: string[] };
    expect(anon.id).toBeNull();
    expect(anon.raw.join(' ')).not.toContain('forged-admin');
    const signIn = await fetch(base + '/signin-with-chatgpt?return_to=%2Fgraph', { redirect: 'manual' });
    expect(signIn.status).toBe(302);
    expect(signIn.headers.get('set-cookie')).toMatch(/HttpOnly; SameSite=Lax/);
    expect(signIn.headers.get('location')).toBe('/graph');
    const cookie = signIn.headers.get('set-cookie')!.split(';')[0];
    const signedIn = await fetch(base + '/api/graph', {
      headers: { cookie: cookie + '; application=kept', 'oai-authenticated-user-id': 'forged-admin' },
    });
    const user = await signedIn.json() as { id: string | null; cookie: string | null };
    expect(user.id).toBe('local_seedy');
    expect(user.cookie).toBe('application=kept');
    // Fetch may normalize Host; the native client actually sends a foreign authority.
    const foreignHost = await new Promise<{ id: string | null }>((resolve, reject) => {
      httpGet(base + '/api/graph', { headers: {
        host: 'public.example', cookie, 'oai-authenticated-user-id': 'forged-admin',
      } }, response => {
        let data = '';
        response.on('data', chunk => { data += chunk; });
        response.on('end', () => resolve(JSON.parse(data)));
        response.on('error', reject);
      }).on('error', reject);
    });
    expect(foreignHost.id).toBeNull();
    expect((await fetch(base + '/signin-with-chatgpt', {
      headers: { origin: 'https://evil.example' }, redirect: 'manual',
    })).status).toBe(403);
    const signOut = await fetch(base + '/signout-with-chatgpt', { headers: { cookie }, redirect: 'manual' });
    expect(signOut.headers.get('set-cookie')).toContain('Max-Age=0');
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});
