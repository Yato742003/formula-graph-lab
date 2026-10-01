// JSON-only BFFs have no cross-origin CORS grant. Also reject sibling origins:
// Sec-Fetch-Site alone does not protect against an untrusted same-site subdomain.
export function isSameOriginRequest(request: Request): boolean {
  const site = request.headers.get('sec-fetch-site');
  if (site && site !== 'same-origin' && site !== 'none') return false;
  const source = request.headers.get('origin') ?? request.headers.get('referer');
  if (source === null) return true; // Non-browser clients; authentication still required.
  try {
    return new URL(source).origin === new URL(request.url).origin;
  } catch {
    return false;
  }
}
