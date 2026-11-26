import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  normalizeArxivHtmlUrl,
  PaperUrlError,
  validatedPaperSource,
} from '../lib/paper-url';
import { signedGraphHeaders } from '../lib/server/graph-service-auth';
import {
  assertDeclaredLengthWithinLimit,
  PayloadTooLargeError,
  readTextLimited,
} from '../lib/server/limited-stream';
import { isSameOriginRequest } from '../lib/server/request-origin';

const digits = (minLength: number, maxLength: number) =>
  fc.stringMatching(new RegExp(`^[0-9]{${minLength},${maxLength}}$`));

const hex64 = fc.stringMatching(/^[a-f0-9]{64}$/);

const validArxivId = fc
  .tuple(
    digits(4, 4),
    digits(4, 5),
    fc.option(fc.integer({ min: 1, max: 99 }), { nil: null }),
  )
  .map(([prefix, suffix, version]) =>
    version === null ? `${prefix}.${suffix}` : `${prefix}.${suffix}v${version}`,
  );

const printableAsciiToken = (minLength: number, maxLength: number) =>
  fc.stringMatching(new RegExp(`^[A-Za-z0-9._:/-]{${minLength},${maxLength}}$`));

function decodeBase64UrlJson(segment: string): Record<string, unknown> {
  const padded = segment
    .replaceAll('-', '+')
    .replaceAll('_', '/')
    .padEnd(Math.ceil(segment.length / 4) * 4, '=');
  return JSON.parse(Buffer.from(padded, 'base64').toString('utf8')) as Record<
    string,
    unknown
  >;
}

async function sha256Hex(input: string): Promise<string> {
  const digest = new Uint8Array(
    await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input)),
  );
  return Array.from(digest, (b) => b.toString(16).padStart(2, '0')).join('');
}

describe('Property-Based Invariants (TypeScript / BFF Security & Provenance)', () => {
  it('normalizeArxivHtmlUrl is idempotent and canonicalizes all valid arXiv URLs', () => {
    fc.assert(
      fc.property(
        fc.constantFrom('arxiv.org', 'export.arxiv.org'),
        fc.constantFrom('abs', 'html'),
        validArxivId,
        fc.constantFrom('', '  ', ' '),
        fc.constantFrom('', '  '),
        fc.constantFrom('', '?ref=pbt', '#sec1'),
        (host, kind, paperId, padL, padR, suffix) => {
          const raw = `${padL}https://${host}/${kind}/${paperId}${suffix}${padR}`;
          const once = normalizeArxivHtmlUrl(raw);
          const twice = normalizeArxivHtmlUrl(once);
          expect(twice).toBe(once);
          expect(once).toBe(`https://arxiv.org/html/${paperId}`);
        },
      ),
      { numRuns: 150 },
    );
  });

  it('normalizeArxivHtmlUrl fails closed on non-arXiv hosts, HTTP, ports, creds, and encoding', () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc
            .tuple(fc.constantFrom('evil.org', 'arxiv.org.evil.test', '127.0.0.1', 'localhost'), validArxivId)
            .map(([host, id]) => `https://${host}/html/${id}`),
          validArxivId.map((id) => `http://arxiv.org/html/${id}`),
          fc
            .tuple(fc.integer({ min: 1, max: 65535 }), validArxivId)
            .map(([port, id]) => `https://arxiv.org:${port}/html/${id}`),
          validArxivId.map((id) => `https://user:pass@arxiv.org/html/${id}`),
          validArxivId.map((id) => `https://arxiv.org/html/${id}%2f..`),
          validArxivId.map((id) => `https://arxiv.org/html\\${id}`),
        ),
        (hostileUrl) => {
          expect(() => normalizeArxivHtmlUrl(hostileUrl)).toThrow(PaperUrlError);
        },
      ),
      { numRuns: 150 },
    );
  });

  it('validatedPaperSource enforces exact local-tex SHA-256 bijection and rejects single-char tampers', () => {
    fc.assert(
      fc.property(
        hex64,
        fc.integer({ min: 0, max: 63 }),
        fc.integer({ min: 2, max: 99 }),
        (hash, tamperPos, wrongVersion) => {
          const id = `tex_${hash.slice(0, 12)}`;
          const url = `urn:fgl:local-tex:${hash}`;
          expect(validatedPaperSource(id, 1, hash, url)).toBe(url);

          // Wrong version must fail closed.
          expect(() => validatedPaperSource(id, wrongVersion, hash, url)).toThrow(TypeError);

          // Single hex-nibble tamper in hash breaks provenance binding.
          const flippedChar = hash[tamperPos] === 'a' ? 'b' : 'a';
          const tamperedHash =
            hash.slice(0, tamperPos) + flippedChar + hash.slice(tamperPos + 1);
          expect(() => validatedPaperSource(id, 1, tamperedHash, url)).toThrow(TypeError);
          expect(() =>
            validatedPaperSource(id, 1, hash, `urn:fgl:local-tex:${tamperedHash}`),
          ).toThrow(TypeError);
        },
      ),
      { numRuns: 120 },
    );
  });

  it('isSameOriginRequest accepts matching origins and rejects sibling subdomains & hostile Sec-Fetch-Site', () => {
    const label = fc.stringMatching(/^[a-z]{3,10}$/);
    fc.assert(
      fc.property(
        label,
        label,
        fc.constantFrom('same-origin', 'none'),
        fc.constantFrom('cross-site', 'same-site'),
        (domain, sub, safeFetchSite, hostileFetchSite) => {
          const targetUrl = `https://${domain}.example/api/imports`;

          const sameOriginReq = new Request(targetUrl, {
            method: 'POST',
            headers: {
              origin: `https://${domain}.example`,
              'sec-fetch-site': safeFetchSite,
            },
          });
          expect(isSameOriginRequest(sameOriginReq)).toBe(true);

          const subdomainReq = new Request(targetUrl, {
            method: 'POST',
            headers: {
              origin: `https://${sub}.${domain}.example`,
              'sec-fetch-site': 'same-origin',
            },
          });
          expect(isSameOriginRequest(subdomainReq)).toBe(false);

          const hostileSiteReq = new Request(targetUrl, {
            method: 'POST',
            headers: {
              origin: `https://${domain}.example`,
              'sec-fetch-site': hostileFetchSite,
            },
          });
          expect(isSameOriginRequest(hostileSiteReq)).toBe(false);

          const schemeDowngradeReq = new Request(targetUrl, {
            method: 'POST',
            headers: {
              origin: `http://${domain}.example`,
              'sec-fetch-site': 'same-origin',
            },
          });
          expect(isSameOriginRequest(schemeDowngradeReq)).toBe(false);
        },
      ),
      { numRuns: 120 },
    );
  });

  it('signedGraphHeaders binds exact claims, SHA-256 body digest, unique jti, and rejects invalid contexts', async () => {
    const allowedOp = fc.constantFrom<['GET' | 'POST', string, string]>(
      ['POST', '/v1/evidence/import-tex', 'graph_write'],
      ['POST', '/v1/contract-reviews', 'graph_write'],
      ['POST', '/v1/search', 'graph_read'],
      ['POST', '/v1/checks/symbolic/quick', 'research_write'],
      ['GET', '/v1/research/campaigns/test', 'research_read'],
    );

    await fc.assert(
      fc.asyncProperty(
        printableAsciiToken(1, 32),
        printableAsciiToken(1, 32),
        printableAsciiToken(32, 96),
        allowedOp,
        fc.string({ maxLength: 200 }),
        async (actorId, workspaceId, serviceToken, [method, path, expectedRole], body) => {
          const baseUrl = new URL('http://127.0.0.1:8000');
          const url = new URL(path, baseUrl);
          const payloadBody = method === 'GET' ? undefined : body;

          const headersA = await signedGraphHeaders(
            { baseUrl, serviceToken },
            url,
            method,
            payloadBody,
            actorId,
            workspaceId,
          );
          const headersB = await signedGraphHeaders(
            { baseUrl, serviceToken },
            url,
            method,
            payloadBody,
            actorId,
            workspaceId,
          );

          const authA = headersA.get('authorization') ?? '';
          const authB = headersB.get('authorization') ?? '';
          expect(authA.startsWith('Bearer fgl1.')).toBe(true);
          const partsA = authA.slice('Bearer '.length).split('.');
          const partsB = authB.slice('Bearer '.length).split('.');
          expect(partsA).toHaveLength(3);

          const claimsA = decodeBase64UrlJson(partsA[1]);
          const claimsB = decodeBase64UrlJson(partsB[1]);

          expect(claimsA.v).toBe(1);
          expect(claimsA.iss).toBe('fgl-web');
          expect(claimsA.aud).toBe('fgl-graph');
          expect(claimsA.actor_id).toBe(actorId);
          expect(claimsA.workspace_id).toBe(workspaceId);
          expect(claimsA.service_role).toBe(expectedRole);
          expect(claimsA.method).toBe(method);
          expect(claimsA.target).toBe(path);
          expect(claimsA.body_sha256).toBe(await sha256Hex(payloadBody ?? ''));
          expect(claimsA.exp).toBe((claimsA.iat as number) + 60);
          expect(claimsA.jti).not.toBe(claimsB.jti);
        },
      ),
      { numRuns: 80 },
    );

    // Fail-closed rejection checks across random unknown paths and cross-origin URLs.
    await fc.assert(
      fc.asyncProperty(
        printableAsciiToken(32, 64),
        fc.stringMatching(/^\/v1\/unknown-[a-z0-9]{1,12}$/),
        async (serviceToken, unknownPath) => {
          const baseUrl = new URL('http://127.0.0.1:8000');
          await expect(
            signedGraphHeaders(
              { baseUrl, serviceToken },
              new URL(unknownPath, baseUrl),
              'POST',
              '{}',
              'actor-1',
              'ws-1',
            ),
          ).rejects.toThrow(TypeError);

          await expect(
            signedGraphHeaders(
              { baseUrl, serviceToken },
              new URL('http://evil.example/v1/search'),
              'POST',
              '{}',
              'actor-1',
              'ws-1',
            ),
          ).rejects.toThrow(TypeError);
        },
      ),
      { numRuns: 50 },
    );
  });

  it('readTextLimited preserves arbitrary multi-byte UTF-8 across random chunk splits and enforces strict byte cap', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.string({ minLength: 0, maxLength: 120 }),
        fc.array(fc.integer({ min: 1, max: 11 }), { minLength: 1, maxLength: 16 }),
        async (text, chunkSizes) => {
          const encoded = new TextEncoder().encode(text);
          const chunks: Uint8Array[] = [];
          let offset = 0;
          let sizeIdx = 0;
          while (offset < encoded.byteLength) {
            const step = chunkSizes[sizeIdx % chunkSizes.length];
            chunks.push(encoded.subarray(offset, Math.min(encoded.byteLength, offset + step)));
            offset += step;
            sizeIdx += 1;
          }

          const makeStream = () =>
            new ReadableStream<Uint8Array>({
              start(controller) {
                for (const chunk of chunks) {
                  controller.enqueue(chunk);
                }
                controller.close();
              },
            });

          // Exact byte budget succeeds and reconstructs original UTF-8 text losslessly.
          const decoded = await readTextLimited(makeStream(), encoded.byteLength);
          expect(decoded).toBe(text);

          // Budget strictly smaller than byte length MUST fail closed with PayloadTooLargeError.
          if (encoded.byteLength > 0) {
            await expect(readTextLimited(makeStream(), encoded.byteLength - 1)).rejects.toThrow(
              PayloadTooLargeError,
            );
            expect(() =>
              assertDeclaredLengthWithinLimit(
                new Headers({ 'content-length': String(encoded.byteLength) }),
                encoded.byteLength - 1,
              ),
            ).toThrow(PayloadTooLargeError);
          }
        },
      ),
      { numRuns: 100 },
    );
  });
});
