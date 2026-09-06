import type {
  EvidenceEntityType,
  EvidenceSearchHit,
  EvidenceSearchInput,
  EvidenceSearchResponse,
  VerificationStatus,
} from '../import-types';
import type { GraphApiConfiguration } from './paper-import';
import {
  assertDeclaredLengthWithinLimit,
  PayloadTooLargeError,
  readTextLimited,
} from './limited-stream';

const MAX_GRAPH_RESPONSE_BYTES = 4 * 1024 * 1024;
const MAX_HITS = 50;
const ARXIV_ID = /^\d{4}\.\d{4,5}$/;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const ENTITY_TYPES = new Set<EvidenceEntityType>([
  'Paper',
  'PaperVersion',
  'Section',
  'Equation',
  'Symbol',
  'Assumption',
  'Claim',
  'Concept',
  'Method',
  'Experiment',
  'Hypothesis',
]);
const VERIFICATION_STATUSES = new Set<VerificationStatus>([
  'reported',
  'draft',
  'invalid',
  'well_typed',
  'numerically_plausible',
  'symbolically_verified',
  'human_reviewed',
]);
const MATCH_SOURCES = new Set(['lexical', 'semantic', 'graph'] as const);
const INPUT_FIELDS = new Set([
  'query',
  'paper_id',
  'version',
  'entity_types',
  'verification_statuses',
  'as_of',
  'center_node_uuid',
  'limit',
  'cursor',
]);

export type SearchErrorCode =
  | 'DATABASE_UNAVAILABLE'
  | 'GRAPH_API_INVALID_RESPONSE'
  | 'GRAPH_API_RESPONSE_TOO_LARGE'
  | 'GRAPH_API_UNAVAILABLE'
  | 'SEARCH_REJECTED';

export class SearchWorkflowError extends Error {
  constructor(
    readonly code: SearchErrorCode,
    readonly status: number,
  ) {
    super(code);
    this.name = 'SearchWorkflowError';
  }
}

export class D1WorkspaceAccess {
  constructor(private readonly database: D1Database) {}

  async isOwned(workspaceId: string, ownerUserId: string): Promise<boolean> {
    const owned = await this.database
      .prepare(
        `SELECT id FROM workspaces
         WHERE id = ? AND owner_user_id = ?`,
      )
      .bind(workspaceId, ownerUserId)
      .first<{ id: string }>();
    return Boolean(owned);
  }
}

export class HttpGraphSearchClient {
  constructor(
    private readonly configuration: GraphApiConfiguration,
    private readonly fetchImplementation: typeof fetch = fetch,
  ) {}

  async search(
    input: EvidenceSearchInput,
    workspaceId: string,
  ): Promise<EvidenceSearchResponse> {
    let response: Response;
    try {
      response = await this.fetchImplementation(
        new URL('/v1/search', this.configuration.baseUrl),
        {
          method: 'POST',
          headers: {
            authorization: `Bearer ${this.configuration.serviceToken}`,
            'content-type': 'application/json',
          },
          body: JSON.stringify({ ...input, workspace_id: workspaceId }),
          redirect: 'manual',
          signal: AbortSignal.timeout(20_000),
        },
      );
    } catch (error) {
      logSearchFailure(error);
      throw new SearchWorkflowError('GRAPH_API_UNAVAILABLE', 502);
    }
    if (!response.ok) {
      void response.body?.cancel();
      throw new SearchWorkflowError(
        response.status >= 400 && response.status < 500
          ? 'SEARCH_REJECTED'
          : 'GRAPH_API_UNAVAILABLE',
        response.status >= 400 && response.status < 500 ? 422 : 502,
      );
    }
    try {
      assertDeclaredLengthWithinLimit(
        response.headers,
        MAX_GRAPH_RESPONSE_BYTES,
      );
      const responseText = await readTextLimited(
        response.body,
        MAX_GRAPH_RESPONSE_BYTES,
      );
      return parseEvidenceSearchResponse(JSON.parse(responseText));
    } catch (error) {
      if (error instanceof PayloadTooLargeError) {
        throw new SearchWorkflowError('GRAPH_API_RESPONSE_TOO_LARGE', 502);
      }
      throw new SearchWorkflowError('GRAPH_API_INVALID_RESPONSE', 502);
    }
  }
}

export function parseEvidenceSearchInput(value: unknown): EvidenceSearchInput {
  const input = record(value);
  if (Object.keys(input).some((field) => !INPUT_FIELDS.has(field))) {
    throw new TypeError('Unknown search field.');
  }
  const query = boundedString(input.query, 500).replaceAll(/\s+/g, ' ').trim();
  const result: EvidenceSearchInput = { query };
  if (input.paper_id !== undefined) {
    const paperId = boundedString(input.paper_id, 32);
    if (!ARXIV_ID.test(paperId)) throw new TypeError('Invalid paper ID.');
    result.paper_id = paperId;
  }
  if (input.version !== undefined) {
    result.version = positiveInteger(input.version, 1_000_000);
  }
  if (input.entity_types !== undefined) {
    result.entity_types = enumArray(input.entity_types, ENTITY_TYPES, 20);
  }
  if (input.verification_statuses !== undefined) {
    result.verification_statuses = enumArray(
      input.verification_statuses,
      VERIFICATION_STATUSES,
      10,
    );
  }
  if (input.as_of !== undefined) {
    const asOf = boundedString(input.as_of, 64);
    if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(asOf) || Number.isNaN(Date.parse(asOf))) {
      throw new TypeError('Search time must be an ISO timestamp with timezone.');
    }
    result.as_of = asOf;
  }
  if (input.center_node_uuid !== undefined) {
    const center = boundedString(input.center_node_uuid, 200);
    if (!UUID.test(center)) throw new TypeError('Invalid center node UUID.');
    result.center_node_uuid = center;
  }
  if (input.limit !== undefined) {
    result.limit = positiveInteger(input.limit, 50);
  }
  if (input.cursor !== undefined) {
    result.cursor = boundedString(input.cursor, 2_048);
  }
  return result;
}

export function emptySearchResponse(): EvidenceSearchResponse {
  return { hits: [], next_cursor: null, semantic_available: false };
}

function parseEvidenceSearchResponse(value: unknown): EvidenceSearchResponse {
  const root = record(value);
  return {
    hits: boundedArray(root.hits, MAX_HITS).map(parseHit),
    next_cursor:
      root.next_cursor === null
        ? null
        : boundedString(root.next_cursor, 2_048),
    semantic_available: booleanValue(root.semantic_available),
  };
}

function parseHit(value: unknown): EvidenceSearchHit {
  const hit = record(value);
  const kind = enumValue(hit.kind, ENTITY_TYPES);
  const verificationStatus = enumValue(
    hit.verification_status,
    VERIFICATION_STATUSES,
  );
  const version = hit.version === null ? null : positiveInteger(hit.version, 1_000_000);
  const validAt = hit.valid_at === null ? null : boundedString(hit.valid_at, 64);
  if (validAt !== null && Number.isNaN(Date.parse(validAt))) {
    throw new TypeError('Invalid result timestamp.');
  }
  const components = record(hit.score_components);
  return {
    uuid: boundedString(hit.uuid, 200),
    kind,
    logical_id: boundedString(hit.logical_id, 500),
    paper_id: boundedString(hit.paper_id, 32),
    version,
    valid_at: validAt,
    verification_status: verificationStatus,
    payload: record(hit.payload),
    episode_uuids: stringArray(hit.episode_uuids, 200, 2_000),
    score: positiveInteger(hit.score, Number.MAX_SAFE_INTEGER),
    match_sources: enumArray(hit.match_sources, MATCH_SOURCES, 3),
    score_components: {
      lexical_rank: nullablePositiveInteger(components.lexical_rank, 250),
      semantic_rank: nullablePositiveInteger(components.semantic_rank, 250),
      graph_rank: nullablePositiveInteger(components.graph_rank, 250),
      graph_distance: nullablePositiveInteger(components.graph_distance, 2),
    },
  };
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('Expected an object.');
  }
  return value as Record<string, unknown>;
}

function boundedString(value: unknown, maxLength: number): string {
  if (
    typeof value !== 'string' ||
    value.trim().length === 0 ||
    value.length > maxLength
  ) {
    throw new TypeError('Invalid string.');
  }
  return value;
}

function boundedArray(value: unknown, maxItems: number): unknown[] {
  if (!Array.isArray(value) || value.length > maxItems) {
    throw new TypeError('Invalid array.');
  }
  return value;
}

function enumValue<T extends string>(value: unknown, allowed: ReadonlySet<T>): T {
  const parsed = boundedString(value, 100) as T;
  if (!allowed.has(parsed)) throw new TypeError('Invalid enum value.');
  return parsed;
}

function enumArray<T extends string>(
  value: unknown,
  allowed: ReadonlySet<T>,
  maxItems: number,
): T[] {
  const result = boundedArray(value, maxItems).map((item) =>
    enumValue(item, allowed),
  );
  if (result.length !== new Set(result).size) {
    throw new TypeError('Duplicate enum value.');
  }
  return result;
}

function stringArray(value: unknown, maxLength: number, maxItems: number): string[] {
  return boundedArray(value, maxItems).map((item) => boundedString(item, maxLength));
}

function positiveInteger(value: unknown, maximum: number): number {
  if (
    typeof value !== 'number' ||
    !Number.isSafeInteger(value) ||
    value < 1 ||
    value > maximum
  ) {
    throw new TypeError('Invalid positive integer.');
  }
  return value;
}

function nullablePositiveInteger(value: unknown, maximum: number): number | null {
  return value === null ? null : positiveInteger(value, maximum);
}

function booleanValue(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new TypeError('Invalid boolean.');
  return value;
}

function logSearchFailure(error: unknown): void {
  if (error instanceof Error) {
    console.error('Graph API search request failed.', {
      name: error.name,
      message: error.message,
    });
    return;
  }
  console.error('Graph API search failed with a non-Error value.');
}
