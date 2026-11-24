import { getChatGPTUser } from '@/app/chatgpt-auth';
import { getD1 } from '@/db';
import { emitBffTelemetry, newBffRequestId } from '@/lib/server/bff-telemetry';
import { isSameOriginRequest } from '@/lib/server/request-origin';
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

function json(body: unknown, status: number, requestId: string, upstreamRequestId?: string) {
  const headers: Record<string, string> = {
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'x-request-id': requestId,
  };
  if (upstreamRequestId) {
    headers['x-upstream-request-id'] = upstreamRequestId;
  }
  return Response.json(body, { status, headers });
}

export async function POST(request: Request) {
  const bffRequestId = newBffRequestId();
  const user = await getChatGPTUser();
  if (!user) return json({ code: 'AUTH_REQUIRED' }, 401, bffRequestId);

  if (!isSameOriginRequest(request)) {
    return json({ code: 'CROSS_SITE_REQUEST_REJECTED' }, 403, bffRequestId);
  }
  const mediaType = request.headers
    .get('content-type')
    ?.split(';', 1)[0]
    .trim()
    .toLowerCase();
  if (mediaType !== 'application/json') {
    return json({ code: 'JSON_REQUIRED' }, 415, bffRequestId);
  }

  let submittedUrl: unknown;
  try {
    assertDeclaredLengthWithinLimit(request.headers, MAX_REQUEST_BYTES);
    const rawBody = await readTextLimited(request.body, MAX_REQUEST_BYTES);
    const body = JSON.parse(rawBody) as unknown;
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return json({ code: 'INVALID_JSON' }, 400, bffRequestId);
    }
    const fields = Object.keys(body);
    if (fields.length !== 1 || fields[0] !== 'url') {
      return json({ code: 'INVALID_REQUEST' }, 400, bffRequestId);
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
      bffRequestId,
    );
  }
  if (typeof submittedUrl !== 'string') {
    return json({ code: 'URL_REQUIRED' }, 400, bffRequestId);
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
      bffRequestId,
    );
  }

  const graphConfiguration = resolveGraphApiConfiguration(
    {
      GRAPH_API_URL: env.GRAPH_API_URL,
      GRAPH_API_SERVICE_TOKEN: env.GRAPH_API_SERVICE_TOKEN,
    },
    !import.meta.env.DEV || env.APP_ENV !== 'development',
  );
  if (!graphConfiguration) {
    emitBffTelemetry('bff_paper_import', {
      bff_request_id: bffRequestId,
      status: 503,
      code: 'GRAPH_API_NOT_CONFIGURED',
    });
    return json({ code: 'GRAPH_API_NOT_CONFIGURED' }, 503, bffRequestId);
  }

  let repository: D1ImportRepository;
  try {
    repository = new D1ImportRepository(getD1());
  } catch {
    emitBffTelemetry('bff_paper_import', {
      bff_request_id: bffRequestId,
      status: 503,
      code: 'DATABASE_UNAVAILABLE',
    });
    return json({ code: 'DATABASE_UNAVAILABLE' }, 503, bffRequestId);
  }

  let upstreamRequestId: string | undefined;
  try {
    const graphClient = new HttpGraphImportClient(graphConfiguration);
    const result = await runPaperImport({
      canonicalUrl,
      userId: user.userId,
      repository,
      graphClient,
      bffRequestId,
    });
    upstreamRequestId = graphClient.lastUpstreamRequestId;
    return json(result, 200, bffRequestId, upstreamRequestId);
  } catch (error) {
    if (error instanceof ImportWorkflowError) {
      return json({ code: error.code }, error.status, bffRequestId, upstreamRequestId);
    }
    return json({ code: 'IMPORT_FAILED' }, 503, bffRequestId, upstreamRequestId);
  }
}
