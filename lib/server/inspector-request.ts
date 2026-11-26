import { getChatGPTUser } from '@/app/chatgpt-auth';
import { getD1 } from '@/db';
import { env } from 'cloudflare:workers';
import { isSameOriginRequest } from './request-origin';
import { D1WorkspaceAccess } from './evidence-search';
import { signedGraphHeaders } from './graph-service-auth';
import { emitBffTelemetry, newBffRequestId, safeUpstreamRequestId } from './bff-telemetry';
import { assertDeclaredLengthWithinLimit, PayloadTooLargeError, readTextLimited } from './limited-stream';
import { resolveGraphApiConfiguration, workspaceIdentifierForUser } from './paper-import';

export const equationUuid = /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i;

export async function inspectorRequest(request: Request, path: string, event: string, limit: number,
  parse?: (body: Record<string, unknown>) => object) {
  const id = newBffRequestId();
  const respond = (data: unknown, status: number, upstreamRequestId?: string) => {
    const headers: Record<string, string> = {
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
      'x-request-id': id,
    };
    if (upstreamRequestId) {
      headers['x-upstream-request-id'] = upstreamRequestId;
    }
    return Response.json(data, { status, headers });
  };
  const user = await getChatGPTUser();
  if (!user) return respond({code: 'AUTH_REQUIRED'}, 401);
  if (!isSameOriginRequest(request)) return respond({code: 'CROSS_SITE_REQUEST_REJECTED'}, 403);
  const workspace = await workspaceIdentifierForUser(user.userId);
  let body: string | undefined;
  const key = request.headers.get('idempotency-key') ?? '';
  if (parse) {
    if (request.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
      return respond({code: 'JSON_REQUIRED'}, 415);
    }
    if (!/^[\x21-\x7e][\x20-\x7e]{15,199}$/.test(key)) return respond({code: 'INVALID_KEY'}, 400);
    try {
      assertDeclaredLengthWithinLimit(request.headers, limit);
      const data = JSON.parse(await readTextLimited(request.body, limit)) as unknown;
      if (!data || typeof data !== 'object' || Array.isArray(data)) {
        throw new Error('Invalid JSON object');
      }
      body = JSON.stringify({...parse(data as Record<string, unknown>), workspace_id: workspace});
    } catch (error) {
      return respond({code: 'INVALID_REQUEST'}, error instanceof PayloadTooLargeError ? 413 : 400);
    }
  }
  try {
    if (!await new D1WorkspaceAccess(getD1()).isOwned(workspace, user.userId)) {
      return respond({code: 'WORKSPACE_NOT_FOUND'}, 404);
    }
    const config = resolveGraphApiConfiguration({
      GRAPH_API_URL: env.GRAPH_API_URL, GRAPH_API_SERVICE_TOKEN: env.GRAPH_API_SERVICE_TOKEN,
    }, !import.meta.env.DEV || env.APP_ENV !== 'development');
    if (!config) return respond({code: 'GRAPH_API_NOT_CONFIGURED'}, 503);
    const url = new URL(path, config.baseUrl);
    const method = parse ? 'POST' : 'GET';
    const response = await fetch(url, {method, body, redirect: 'manual',
      headers: await signedGraphHeaders(config, url, method, body, user.userId, workspace,
        parse ? {'idempotency-key': key} : {}), signal: AbortSignal.timeout(30000)});
    const upstreamRequestId = safeUpstreamRequestId(response.headers);
    emitBffTelemetry(event, {
      bff_request_id: id,
      upstream_request_id: upstreamRequestId,
      workspace_id: workspace,
      status: response.status,
    });
    if (!response.ok) {
      await response.body?.cancel();
      return respond(
        {code: 'INSPECTOR_REQUEST_FAILED'},
        response.status >= 400 ? response.status : 502,
        upstreamRequestId,
      );
    }
    assertDeclaredLengthWithinLimit(response.headers, 256 * 1024);
    return respond(JSON.parse(await readTextLimited(response.body, 256 * 1024)), 200, upstreamRequestId);
  } catch {
    return respond({code: 'SERVICE_UNAVAILABLE'}, 503);
  }
}
