import { getChatGPTUser } from '@/app/chatgpt-auth';
import { getD1 } from '@/db';
import {
  D1WorkspaceAccess,
  emptySearchResponse,
  HttpGraphSearchClient,
  parseEvidenceSearchInput,
  SearchWorkflowError,
} from '@/lib/server/evidence-search';
import {
  assertDeclaredLengthWithinLimit,
  PayloadTooLargeError,
  readTextLimited,
} from '@/lib/server/limited-stream';
import {
  resolveGraphApiConfiguration,
  workspaceIdentifierForUser,
} from '@/lib/server/paper-import';
import { env } from 'cloudflare:workers';

const MAX_REQUEST_BYTES = 8 * 1024;

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

  let input;
  try {
    assertDeclaredLengthWithinLimit(request.headers, MAX_REQUEST_BYTES);
    const body = await readTextLimited(request.body, MAX_REQUEST_BYTES);
    input = parseEvidenceSearchInput(JSON.parse(body));
  } catch (error) {
    return json(
      {
        code:
          error instanceof PayloadTooLargeError
            ? 'REQUEST_TOO_LARGE'
            : 'INVALID_SEARCH_REQUEST',
      },
      error instanceof PayloadTooLargeError ? 413 : 400,
    );
  }

  const workspaceId = await workspaceIdentifierForUser(user.userId);
  try {
    const owned = await new D1WorkspaceAccess(getD1()).isOwned(
      workspaceId,
      user.userId,
    );
    if (!owned) return json(emptySearchResponse(), 200);
  } catch {
    return json({ code: 'DATABASE_UNAVAILABLE' }, 503);
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

  try {
    const result = await new HttpGraphSearchClient(graphConfiguration).search(
      input,
      workspaceId,
    );
    return json(result, 200);
  } catch (error) {
    if (error instanceof SearchWorkflowError) {
      return json({ code: error.code }, error.status);
    }
    return json({ code: 'SEARCH_FAILED' }, 503);
  }
}
