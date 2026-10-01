import type { GraphApiConfiguration } from './paper-import';

const encoder = new TextEncoder();
const READ_PATHS = new Set([
  '/v1/search', '/v1/graphs/snapshot', '/v1/extractions/preview',
  '/v1/formulas/parse', '/v1/formulas/compare',
]);

// Server-only request attestation, not a user session/JWT. Never send the key
// or this token to the browser. Native WebCrypto also runs in Cloudflare Workers.
// ponytail: one HMAC key, ceiling: mutually trusted web/API, upgrade: asymmetric
// issuer/key ring when services require independent trust or staged rotation.
export async function signedGraphHeaders(
  configuration: GraphApiConfiguration,
  url: URL,
  method: 'GET' | 'POST',
  body: string | undefined,
  actorId: string,
  workspaceId: string,
  extraHeaders: Record<string, string> = {},
): Promise<Headers> {
  if (!/^[\x21-\x7e]{1,200}$/.test(actorId) ||
      !/^[\x21-\x7e]{1,200}$/.test(workspaceId) ||
      !/^[\x21-\x7e]{32,256}$/.test(configuration.serviceToken) ||
      url.origin !== configuration.baseUrl.origin) {
    throw new TypeError('Invalid service authentication context.');
  }
  const headers = new Headers(extraHeaders);
  if (headers.has('idempotency-key') && headers.has('x-idempotency-key')) {
    throw new TypeError('Ambiguous idempotency key.');
  }
  let serviceRole: string;
  if (url.pathname.startsWith('/v1/research/')) {
    serviceRole = method === 'GET' ? 'research_read' : 'research_write';
  } else if (method === 'POST' && READ_PATHS.has(url.pathname)) {
    serviceRole = 'graph_read';
  } else if (method === 'POST' && ['/v1/imports', '/v1/contract-reviews'].includes(url.pathname)) {
    serviceRole = 'graph_write';
  } else {
    throw new TypeError('Unknown service operation.');
  }
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(body ?? '')));
  const now = Math.floor(Date.now() / 1000);
  const payload = base64url(encoder.encode(JSON.stringify({
    v: 1, iss: 'fgl-web', aud: 'fgl-graph',
    actor_id: actorId, actor_role: 'researcher', workspace_id: workspaceId,
    service_role: serviceRole, method, target: url.pathname + url.search,
    body_sha256: Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join(''),
    idempotency_key: headers.get('idempotency-key') ?? headers.get('x-idempotency-key') ?? '',
    iat: now, exp: now + 60, jti: crypto.randomUUID(),
  })));
  const key = await crypto.subtle.importKey('raw', encoder.encode(configuration.serviceToken),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const signature = new Uint8Array(await crypto.subtle.sign('HMAC', key,
    encoder.encode('fgl-service.v1\0' + payload)));
  headers.set('authorization', `Bearer fgl1.${payload}.${base64url(signature)}`);
  headers.set('x-fgl-actor-id', actorId);
  headers.set('x-fgl-actor-role', 'researcher');
  headers.set('x-fgl-workspace-id', workspaceId);
  if (body !== undefined) headers.set('content-type', 'application/json');
  return headers;
}

function base64url(bytes: Uint8Array): string {
  return btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(''))
    .replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
}
