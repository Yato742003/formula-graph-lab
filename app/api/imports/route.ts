import { getChatGPTUser } from '@/app/chatgpt-auth';
import { getD1 } from '@/db';
import { env } from 'cloudflare:workers';
import { normalizeArxivHtmlUrl, PaperUrlError } from '@/lib/paper-url';
import {
  assertDeclaredLengthWithinLimit,
  PayloadTooLargeError,
  readTextLimited,
} from '@/lib/server/limited-stream';
import {
  D1ImportRepository,
  HttpGraphImportClient,
  ImportWorkflowError,
  resolveGraphApiConfiguration,
  runPaperImport,
} from '@/lib/server/paper-import';

const MAX_REQUEST_BYTES = 4 * 1024;

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

  if (request.headers.get('sec-fetch-site') === 'cross-site') {
    return json({ code: 'CROSS_SITE_REQUEST_REJECTED' }, 403);
  }
  const mediaType = request.headers
    .get('content-type')
    ?.split(';', 1)[0]
    .trim()
    .toLowerCase();
  if (mediaType !== 'application/json') {
    return json({ code: 'JSON_REQUIRED' }, 415);
  }

  let submittedUrl: unknown;
  try {
    assertDeclaredLengthWithinLimit(request.headers, MAX_REQUEST_BYTES);
    const rawBody = await readTextLimited(request.body, MAX_REQUEST_BYTES);
    const body = JSON.parse(rawBody) as unknown;
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return json({ code: 'INVALID_JSON' }, 400);
    }
    const fields = Object.keys(body);
    if (fields.length !== 1 || fields[0] !== 'url') {
      return json({ code: 'INVALID_REQUEST' }, 400);
    }
    submittedUrl = (body as { url?: unknown }).url;
  } catch (error) {
    return json(
      {
        code:
          error instanceof PayloadTooLargeError
            ? 'REQUEST_TOO_LARGE'
            : 'INVALID_JSON',
      },
      error instanceof PayloadTooLargeError ? 413 : 400,
    );
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

  const graphConfiguration = resolveGraphApiConfiguration(
    {
      GRAPH_API_URL: env.GRAPH_API_URL,
      GRAPH_API_SERVICE_TOKEN: env.GRAPH_API_SERVICE_TOKEN,
    },
    env.APP_ENV !== 'development',
  );
  if (!graphConfiguration) {
    return json({ code: 'GRAPH_API_NOT_CONFIGURED' }, 503);
  }

  let repository: D1ImportRepository;
  try {
    repository = new D1ImportRepository(getD1());
  } catch {
    return json({ code: 'DATABASE_UNAVAILABLE' }, 503);
  }

  try {
    const result = await runPaperImport({
      canonicalUrl,
      userId: user.userId,
      repository,
      graphClient: new HttpGraphImportClient(graphConfiguration),
    });
    return json(result, 200);
  } catch (error) {
    if (error instanceof ImportWorkflowError) {
      return json({ code: error.code }, error.status);
    }
    return json({ code: 'IMPORT_FAILED' }, 503);
  }
}
