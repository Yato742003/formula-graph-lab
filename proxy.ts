import { NextResponse } from 'next/server';
import { env } from 'cloudflare:workers';

export function proxy(request: Request) {
  const requestHeaders = new Headers(request.headers);
  // Client-supplied policy/nonce must never choose the SSR script authorization.
  requestHeaders.delete('content-security-policy');
  requestHeaders.delete('content-security-policy-report-only');
  requestHeaders.delete('x-nonce');
  const sites = env.FGL_TRUST_SITES_IDENTITY === 'true';
  let policy: string | undefined;
  if (import.meta.env.PROD) {
    const nonce = crypto.randomUUID().replaceAll('-', '');
    policy = [
      "default-src 'self'",
      `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
      "script-src-attr 'none'",
      `style-src 'self' 'nonce-${nonce}'`,
      // ponytail: KaTeX/React Flow need dynamic geometry style attributes,
      // ceiling: CSS-only inline allowance, upgrade: strict style hashes if
      // those renderers stop generating unbounded runtime geometry values.
      "style-src-attr 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self'",
      "connect-src 'self'",
      "worker-src 'self' blob:",
      "object-src 'none'",
      "base-uri 'none'",
      "form-action 'self'",
      sites ? 'frame-ancestors https://chatgpt.com' : "frame-ancestors 'none'",
    ].join('; ');
    requestHeaders.set('x-nonce', nonce);
    requestHeaders.set('content-security-policy', policy);
  }
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  if (policy) response.headers.set('content-security-policy', policy);
  response.headers.set('x-content-type-options', 'nosniff');
  response.headers.set('referrer-policy', 'no-referrer');
  response.headers.set('permissions-policy', 'camera=(), microphone=(), geolocation=()');
  response.headers.set('cache-control', 'no-store');
  if (!sites) response.headers.set('x-frame-options', 'DENY');
  if (new URL(request.url).protocol === 'https:') {
    response.headers.set('strict-transport-security', 'max-age=31536000');
  }
  // Development HMR needs eval/websocket styles: no claim of a dev CSP gate.
  return response;
}

export const config = { matcher: ['/((?!assets/|favicon\\.|_vinext/).*)'] };
