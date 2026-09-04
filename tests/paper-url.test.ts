import { describe, expect, it } from 'vitest';
import { normalizeArxivHtmlUrl, PaperUrlError } from '../lib/paper-url';

describe('normalizeArxivHtmlUrl', () => {
  it.each([
    [
      'https://arxiv.org/html/1706.03762',
      'https://arxiv.org/html/1706.03762',
    ],
    [
      'https://arxiv.org/abs/1706.03762v7?download=1',
      'https://arxiv.org/html/1706.03762v7',
    ],
    [
      'https://export.arxiv.org/html/2402.08954',
      'https://arxiv.org/html/2402.08954',
    ],
  ])('normalizes %s', (value, expected) => {
    expect(normalizeArxivHtmlUrl(value)).toBe(expected);
  });

  it.each([
    'http://arxiv.org/html/1706.03762',
    'https://arxiv.org.evil.example/html/1706.03762',
    'https://user@arxiv.org/html/1706.03762',
    'https://arxiv.org:443/html/1706.03762',
    'https://arxiv.org/pdf/1706.03762',
    'https://arxiv.org/html/1706%2e03762',
  ])('rejects unsafe URL %s', (value) => {
    expect(() => normalizeArxivHtmlUrl(value)).toThrow(PaperUrlError);
  });
});
