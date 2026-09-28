import { describe, expect, it } from 'vitest';
import {
  assertDeclaredLengthWithinLimit,
  PayloadTooLargeError,
  readTextLimited,
} from '../lib/server/limited-stream';

describe('bounded HTTP payloads', () => {
  it('rejects an oversized declared body before reading', () => {
    const headers = new Headers({ 'content-length': '4097' });
    expect(() => assertDeclaredLengthWithinLimit(headers, 4096)).toThrow(
      PayloadTooLargeError,
    );
  });

  it('counts streamed UTF-8 bytes instead of JavaScript characters', async () => {
    const body = new Response('🧪').body;
    await expect(readTextLimited(body, 3)).rejects.toBeInstanceOf(
      PayloadTooLargeError,
    );
    await expect(readTextLimited(new Response('🧪').body, 4)).resolves.toBe(
      '🧪',
    );
  });
});
