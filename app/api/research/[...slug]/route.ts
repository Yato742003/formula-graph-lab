import { getChatGPTUser } from '@/app/chatgpt-auth';
import { getD1 } from '@/db';
import { isSameOriginRequest } from '@/lib/server/request-origin';
import { D1WorkspaceAccess } from '@/lib/server/evidence-search';
import { signedGraphHeaders } from '@/lib/server/graph-service-auth';
import { assertDeclaredLengthWithinLimit, PayloadTooLargeError, readTextLimited } from '@/lib/server/limited-stream';
import { resolveGraphApiConfiguration, workspaceIdentifierForUser } from '@/lib/server/paper-import';
import { env } from 'cloudflare:workers';

function json(body: unknown, status: number) {
  return Response.json(body, { status, headers: {
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
  } });
}

type Context = { params: Promise<{ slug: string[] }> };

async function forward(request: Request, { params }: Context) {
  const user = await getChatGPTUser();
  if (!user) return json({ code: 'AUTH_REQUIRED' }, 401);
  if (!isSameOriginRequest(request)) {
    return json({ code: 'CROSS_SITE_REQUEST_REJECTED' }, 403);
  }
  const { slug } = await params;
  if (!slug.length || slug.some(part => !/^[a-zA-Z0-9_-]{1,200}$/.test(part))) {
    return json({ code: 'UNKNOWN_RESEARCH_ROUTE' }, 404);
  }
  const [collection, identifier, action] = slug;
  const collections = ['problems', 'lineage', 'compatibility', 'candidates', 'proposals', 'evolution', 'ops'];
  const isRead = request.method === 'GET';
  const isCompile = !isRead && collection === 'candidates' &&
    identifier === 'compile' && slug.length === 2;
  const isProposalReview = !isRead && collection === 'proposals' &&
    Boolean(identifier && /^prop_[a-f0-9]{32}$/.test(identifier)) &&
    action === 'reviews' && slug.length === 3;
  const isProposalGenerate = !isRead && collection === 'proposals' &&
    identifier === 'generate' && slug.length === 2;
  const isProposalCapabilities = isRead && collection === 'proposals' &&
    identifier === 'capabilities' && slug.length === 2;
  const isAdmission = !isRead && collection === 'candidates' &&
    Boolean(identifier && /^[a-zA-Z0-9_-]{1,200}$/.test(identifier)) &&
    action === 'admission' && slug.length === 3;
  const isVerification = !isRead && collection === 'candidates' &&
    Boolean(identifier && /^[a-zA-Z0-9_-]{1,200}$/.test(identifier)) &&
    action === 'verify' && slug.length === 3;
  const isNumericalFixture = !isRead && collection === 'candidates' &&
    Boolean(identifier && /^cand_[a-f0-9]{32}$/.test(identifier)) &&
    action === 'numerical-fixture' && slug.length === 3;
  const isResearchCase = !isRead && collection === 'candidates' &&
    Boolean(identifier && /^cand_[a-f0-9]{32}$/.test(identifier)) &&
    action === 'research-case' && slug.length === 3;
  const isProtocolReview = !isRead && collection === 'candidates' &&
    Boolean(identifier && /^cand_[a-f0-9]{32}$/.test(identifier)) &&
    action === 'research-case' && slug[3] === 'reviews' && slug.length === 4;
  const isReplayBundle = isRead && collection === 'candidates' &&
    Boolean(identifier && /^cand_[a-f0-9]{32}$/.test(identifier)) &&
    slug[2] === 'activities' && Boolean(slug[3] && /^act_[a-f0-9]{32}$/.test(slug[3])) &&
    slug[4] === 'bundle' && slug.length === 5;
  const isReplayReport = isRead && collection === 'candidates' &&
    Boolean(identifier && /^cand_[a-f0-9]{32}$/.test(identifier)) &&
    slug[2] === 'activities' && Boolean(slug[3] && /^act_[a-f0-9]{32}$/.test(slug[3])) &&
    slug[4] === 'report' && slug.length === 5;
  const isEvolutionId = Boolean(identifier && /^evo_[a-f0-9]{32}$/.test(identifier));
  const isEvolutionReport = isRead && collection === 'evolution' && isEvolutionId &&
    action === 'report' && slug.length === 3;
  const isEvolutionFinalists = !isRead && collection === 'evolution' && isEvolutionId &&
    action === 'finalists' && slug.length === 3;
  const isEvolutionStop = !isRead && collection === 'evolution' && isEvolutionId &&
    action === 'stop' && slug.length === 3;
  const isEvolutionGeneration = !isRead && collection === 'evolution' && isEvolutionId &&
    action === 'generation' && slug.length === 3;
  const isEvolutionConfirm = !isRead && collection === 'evolution' && isEvolutionId &&
    action === 'confirm' && slug.length === 3;
  const isEvolutionBundle = isRead && collection === 'evolution' && isEvolutionId &&
    action === 'bundle' && slug.length === 3;
  const isOps = collection === 'ops' && (
    (isRead && identifier === 'dashboard' && slug.length === 2) ||
    (!isRead && identifier === 'jobs' && action === 'recover' && slug.length === 3)
  );
  const validRoute = isOps || isCompile || isProposalReview || isProposalGenerate || isProposalCapabilities || isAdmission || isVerification || isNumericalFixture || isResearchCase || isProtocolReview || isReplayBundle || isReplayReport || isEvolutionReport || isEvolutionFinalists || isEvolutionStop || isEvolutionGeneration || isEvolutionConfirm || isEvolutionBundle || (
    collection === 'proposals'
      ? (isRead && slug.length === 1) || isProposalReview || isProposalGenerate || isProposalCapabilities
      : collection === 'evolution'
      ? (isRead && (slug.length === 1 || (slug.length === 2 && isEvolutionId))) ||
        (!isRead && slug.length === 1)
      : collections.includes(collection) && (
    (isRead && (slug.length === 1 || (collection !== 'candidates' && slug.length === 2))) ||
    (!isRead && (slug.length === 1 ||
      (slug.length === 2 && collection === 'lineage' && identifier === 'coverage') ||
      (slug.length === 3 && collection !== 'problems' && action === 'reviews')))
  ));
  if (!validRoute) return json({ code: 'UNKNOWN_RESEARCH_ROUTE' }, 404);

  const url = new URL(request.url);
  const queryKeys = identifier === 'traverse' && collection === 'lineage'
    ? ['endpoint_kind', 'endpoint_id', 'endpoint_version', 'direction', 'relation_type', 'max_depth', 'max_nodes']
    : isReplayBundle ? ['bundle_hash']
    : identifier === 'sources' && collection === 'lineage' ? ['limit', 'offset', 'kind', 'source_id']
    : identifier === 'compare' && collection === 'problems' ? ['left_spec_id', 'right_spec_id']
    : identifier === 'readiness' && collection === 'problems' ? ['spec_id']
    : slug.length === 1 ? ['limit', 'offset', ...(collection === 'lineage' ? ['relation_type'] : [])] : [];
  const expectedBundleHash = url.searchParams.get('bundle_hash');
  if (isReplayBundle && expectedBundleHash !== null && !/^[a-f0-9]{64}$/.test(expectedBundleHash)) {
    return json({ code: 'INVALID_RESEARCH_QUERY' }, 400);
  }
  if ([...url.searchParams.keys()].some(key =>
    !isRead || !queryKeys.includes(key) || url.searchParams.getAll(key).length !== 1) ||
      url.search.length > 2048) {
    return json({ code: 'INVALID_RESEARCH_QUERY' }, 400);
  }

  let body: string | undefined;
  let writeInput: Record<string, unknown> | undefined;
  let key = '';
  if (!isRead) {
    if (request.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
      return json({ code: 'JSON_REQUIRED' }, 415);
    }
    key = request.headers.get('x-idempotency-key')?.trim() ?? '';
    if (!/^[\x20-\x7e]{1,200}$/.test(key)) {
      return json({ code: 'IDEMPOTENCY_KEY_REQUIRED' }, 400);
    }
    try {
      assertDeclaredLengthWithinLimit(request.headers, 64 * 1024);
      body = await readTextLimited(request.body, 64 * 1024);
      const input = JSON.parse(body);
      const allowed = isOps
        ? []
        : isCompile
        ? ['spec_id', 'mapping_id', 'transform', 'parent_candidate_id', 'proposal_id']
        : isEvolutionFinalists ? ['finalist_ids']
        : isEvolutionStop ? []
        : isEvolutionGeneration ? ['seed_candidate_id']
        : isEvolutionConfirm ? []
        : isProtocolReview ? ['result_id', 'decision', 'notes']
        : isVerification ? []
        : isNumericalFixture ? ['seed']
        : isResearchCase ? []
        : isAdmission ? ['action', 'claim_scope']
        : isProposalGenerate ? ['spec_id', 'parent_ids', 'source_span_ids', 'research_question']
        : collection === 'lineage' && identifier === 'coverage'
        ? ['paper_id', 'version', 'title', 'source_reference', 'first_publication_at', 'version_published_at', 'venue_published_at']
        : action === 'reviews' ? ['decision', 'notes']
        : collection === 'problems' ? ['definition', 'parent_spec_id']
        : collection === 'evolution' ? ['spec_id']
        : collection === 'compatibility' ? ['mapping']
        : ['relation_type', 'source', 'target', 'evidence', 'description'];
      if (!input || typeof input !== 'object' || Array.isArray(input) ||
          Object.keys(input).some(field => !allowed.includes(field))) {
        return json({ code: 'INVALID_RESEARCH_REQUEST' }, 400);
      }
      writeInput = input as Record<string, unknown>;
    } catch (error) {
      return json({ code: error instanceof PayloadTooLargeError ? 'REQUEST_TOO_LARGE' : 'INVALID_RESEARCH_REQUEST' },
        error instanceof PayloadTooLargeError ? 413 : 400);
    }
  }
  const workspaceId = await workspaceIdentifierForUser(user.userId);
  try {
    if (!await new D1WorkspaceAccess(getD1()).isOwned(workspaceId, user.userId)) {
      return json({ code: 'WORKSPACE_NOT_FOUND' }, 404);
    }
  } catch {
    return json({ code: 'DATABASE_UNAVAILABLE' }, 503);
  }
  const configuration = resolveGraphApiConfiguration({
    GRAPH_API_URL: env.GRAPH_API_URL,
    GRAPH_API_SERVICE_TOKEN: env.GRAPH_API_SERVICE_TOKEN,
  }, !import.meta.env.DEV || env.APP_ENV !== 'development');
  if (!configuration) return json({ code: 'GRAPH_API_NOT_CONFIGURED' }, 503);
  const upstreamBody = isCompile
    ? JSON.stringify({ ...writeInput, workspace_id: workspaceId })
    : body;
  try {
    const upstreamUrl = new URL('/v1/research/' + slug.join('/') + url.search, configuration.baseUrl);
    const method = isRead ? 'GET' : 'POST';
    const response = await fetch(upstreamUrl, {
      method,
      headers: await signedGraphHeaders(configuration, upstreamUrl, method, upstreamBody,
        user.userId, workspaceId, !isRead ? { 'x-idempotency-key': key } : {}),
      body: upstreamBody,
      redirect: 'manual',
      signal: AbortSignal.timeout(isProposalGenerate ? 100_000 : isEvolutionConfirm ? 75_000 : isResearchCase || isEvolutionGeneration ? 45_000 : isNumericalFixture ? 45_000 : 20_000),
    });
    if (response.status < 200 || response.status >= 300 && response.status < 400) {
      await response.body?.cancel();
      return json({ code: 'UPSTREAM_INVALID_RESPONSE' }, 502);
    }
    assertDeclaredLengthWithinLimit(response.headers, 4 * 1024 * 1024);
    const data = JSON.parse(await readTextLimited(response.body, 4 * 1024 * 1024));
    return json(data, response.status);
  } catch (error) {
    return json({ code: error instanceof PayloadTooLargeError ? 'UPSTREAM_RESPONSE_TOO_LARGE' : 'UPSTREAM_UNAVAILABLE' }, 502);
  }
}

export const GET = forward;
export const POST = forward;
