import { expect, it } from 'vitest';
import { isSameOriginRequest } from '../lib/server/request-origin';

it('rejects cross-origin and same-site sibling requests, with a referer fallback', () => {
  const rejected: Record<string, string>[] = [
    { origin: 'https://evil.example', 'sec-fetch-site': 'same-site' },
    { origin: 'null' }, { origin: 'https://evil.example' },
    { referer: 'https://evil.example/form' }, { 'sec-fetch-site': 'cross-site' },
  ];
  for (const headers of rejected) {
    expect(isSameOriginRequest(new Request('https://app.example/api/imports', { headers }))).toBe(false);
  }
  const accepted: Record<string, string>[] = [{}, { origin: 'https://app.example' },
    { referer: 'https://app.example/graph', 'sec-fetch-site': 'same-origin' }];
  for (const headers of accepted) {
    expect(isSameOriginRequest(new Request('https://app.example/api/imports', { headers }))).toBe(true);
  }
});
