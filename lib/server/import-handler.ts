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

export async function handleImport(request: Request, localTex: boolean) {
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

  const limit = localTex ? 540 * 1024 : MAX_REQUEST_BYTES;
  const event = localTex ? 'bff_tex_import' : 'bff_paper_import';
  let canonicalUrl: string;
  let tex: {tex_source: string; title?: string; filename?: string} | undefined;
  try {
    assertDeclaredLengthWithinLimit(request.headers, limit);
    const rawBody = await readTextLimited(request.body, limit);
    const body = JSON.parse(rawBody) as unknown;
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return json({ code: 'INVALID_JSON' }, 400, bffRequestId);
    }
    const fields = Object.keys(body);
    const data = body as Record<string, unknown>;
    if (localTex) {
      if (fields.some(k => !['tex_source', 'title', 'filename'].includes(k)) ||
          typeof data.tex_source !== 'string' || !data.tex_source.trim() ||
          (data.title !== undefined && (typeof data.title !== 'string' || data.title.length > 2000)) ||
          (data.filename !== undefined && (typeof data.filename !== 'string' || data.filename.length > 255))) {
        return json({code: 'INVALID_REQUEST'}, 400, bffRequestId);
      }
      const bytes = new TextEncoder().encode(data.tex_source);
      if (bytes.length > 512 * 1024) return json({code: 'REQUEST_TOO_LARGE'}, 413, bffRequestId);
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
      canonicalUrl = 'urn:fgl:local-tex:' + Array.from(digest, b => b.toString(16).padStart(2, '0')).join('');
      tex = {tex_source: data.tex_source, title: data.title, filename: data.filename};
    } else {
      if (fields.length !== 1 || fields[0] !== 'url') return json({code: 'INVALID_REQUEST'}, 400, bffRequestId);
      if (typeof data.url !== 'string') return json({code: 'URL_REQUIRED'}, 400, bffRequestId);
      canonicalUrl = normalizeArxivHtmlUrl(data.url);
    }
  } catch (error) {
    if (error instanceof PaperUrlError) return json({code: 'UNSAFE_PAPER_URL', message: error.message}, 400, bffRequestId);
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
  const graphConfiguration = resolveGraphApiConfiguration(
    {
      GRAPH_API_URL: env.GRAPH_API_URL,
      GRAPH_API_SERVICE_TOKEN: env.GRAPH_API_SERVICE_TOKEN,
    },
    !import.meta.env.DEV || env.APP_ENV !== 'development',
  );
  if (!graphConfiguration) {
    emitBffTelemetry(event, {
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
    emitBffTelemetry(event, {
      bff_request_id: bffRequestId,
      status: 503,
      code: 'DATABASE_UNAVAILABLE',
    });
    return json({ code: 'DATABASE_UNAVAILABLE' }, 503, bffRequestId);
  }

  let upstreamRequestId: string | undefined;
  try {
    const graphClient = new HttpGraphImportClient(graphConfiguration, fetch, tex);
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
