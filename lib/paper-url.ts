const ALLOWED_HOSTS = new Set(['arxiv.org', 'export.arxiv.org']);
const MODERN_ARXIV_ID = /^(\d{4}\.\d{4,5})(v\d+)?$/;

export class PaperUrlError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PaperUrlError';
  }
}

export function normalizeArxivHtmlUrl(value: string): string {
  const candidate = value.trim();
  if (!candidate) throw new PaperUrlError('Paper URL is required.');
  if (/[\\\r\n\t%]/.test(candidate)) {
    throw new PaperUrlError('Encoded or ambiguous URLs are not accepted.');
  }
  const hasExplicitPort = /^https:\/\/[^/?#]+:\d+(?:[/?#]|$)/i.test(
    candidate,
  );
  if (hasExplicitPort) {
    throw new PaperUrlError('Custom ports are not allowed.');
  }

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new PaperUrlError('Enter a valid paper URL.');
  }

  if (parsed.protocol !== 'https:') {
    throw new PaperUrlError('Only HTTPS paper URLs are accepted.');
  }
  if (!ALLOWED_HOSTS.has(parsed.hostname)) {
    throw new PaperUrlError('Only arxiv.org HTML papers are supported in V1.');
  }
  if (parsed.username || parsed.password || parsed.port) {
    throw new PaperUrlError('Credentials and custom ports are not allowed.');
  }

  const path = parsed.pathname.split('/').filter(Boolean);
  if (
    path.length !== 2 ||
    !['abs', 'html'].includes(path[0]) ||
    !MODERN_ARXIV_ID.test(path[1])
  ) {
    throw new PaperUrlError(
      'Use an arXiv /html/<paper-id> or /abs/<paper-id> URL.',
    );
  }

  return `https://arxiv.org/html/${path[1]}`;
}
