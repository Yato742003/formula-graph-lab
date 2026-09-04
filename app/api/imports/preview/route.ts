import { getChatGPTUser } from '@/app/chatgpt-auth';
import { normalizeArxivHtmlUrl, PaperUrlError } from '@/lib/paper-url';

const MAX_REQUEST_BYTES = 4 * 1024;
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;

function json(body: unknown, status: number) {
  return Response.json(body, {
    status,
    headers: {
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
    },
  });
}

export async function POST(request: Request) {
  const user = await getChatGPTUser();
  if (!user) return json({ code: 'AUTH_REQUIRED' }, 401);

  const declaredLength = Number(request.headers.get('content-length') ?? 0);
  if (declaredLength > MAX_REQUEST_BYTES) {
    return json({ code: 'REQUEST_TOO_LARGE' }, 413);
  }

  let submittedUrl: unknown;
  try {
    const body = (await request.json()) as { url?: unknown };
    submittedUrl = body.url;
  } catch {
    return json({ code: 'INVALID_JSON' }, 400);
  }
  if (typeof submittedUrl !== 'string') {
    return json({ code: 'URL_REQUIRED' }, 400);
  }

  let canonicalUrl: string;
  try {
    canonicalUrl = normalizeArxivHtmlUrl(submittedUrl);
  } catch (error) {
    return json(
      {
        code: 'UNSAFE_PAPER_URL',
        message:
          error instanceof PaperUrlError
            ? error.message
            : 'Paper URL is not supported.',
      },
      400,
    );
  }

  const graphApiUrl =
    process.env.GRAPH_API_URL ??
    (process.env.NODE_ENV === 'development'
      ? 'http://127.0.0.1:8000'
      : undefined);
  if (!graphApiUrl) {
    return json({ code: 'GRAPH_API_NOT_CONFIGURED' }, 503);
  }

  const headers = new Headers({ 'content-type': 'application/json' });
  if (process.env.GRAPH_API_SERVICE_TOKEN) {
    headers.set(
      'authorization',
      `Bearer ${process.env.GRAPH_API_SERVICE_TOKEN}`,
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      new URL('/v1/extractions/preview', graphApiUrl),
      {
        method: 'POST',
        headers,
        body: JSON.stringify({ url: canonicalUrl }),
        redirect: 'error',
        signal: AbortSignal.timeout(15_000),
      },
    );
  } catch {
    return json({ code: 'GRAPH_API_UNAVAILABLE' }, 502);
  }

  const responseText = await upstream.text();
  if (responseText.length > MAX_RESPONSE_BYTES) {
    return json({ code: 'GRAPH_API_RESPONSE_TOO_LARGE' }, 502);
  }
  if (!upstream.ok) {
    return json(
      {
        code: 'EXTRACTION_FAILED',
        upstreamStatus: upstream.status,
      },
      upstream.status >= 500 ? 502 : upstream.status,
    );
  }

  try {
    return json(JSON.parse(responseText), 200);
  } catch {
    return json({ code: 'GRAPH_API_INVALID_RESPONSE' }, 502);
  }
}
