import type {
  EvidenceEntityType,
  EvidenceGraphEdge,
  EvidenceGraphNode,
  EvidenceGraphSnapshotResponse,
  EvidenceRelationType,
  PersistedPaperSummary,
  VerificationStatus,
} from '../import-types';
import { normalizeArxivHtmlUrl } from '../paper-url';
import type { GraphApiConfiguration } from './paper-import';
import {
  assertDeclaredLengthWithinLimit,
  PayloadTooLargeError,
  readTextLimited,
} from './limited-stream';

const MAX_GRAPH_RESPONSE_BYTES = 4 * 1024 * 1024;
const MAX_GRAPH_NODES = 500;
const MAX_GRAPH_EDGES = 1_500;
const ARXIV_ID = /^\d{4}\.\d{4,5}$/;
const SHA256 = /^[a-f0-9]{64}$/;
const NODE_UUID =
  /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
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
const RELATION_TYPES = new Set<EvidenceRelationType>([
  'has_version',
  'contains',
  'defines',
  'uses',
  'assumes',
  'cites',
  'makes_claim',
  'about',
  'derived_from',
  'approximates',
  'generalizes',
  'equivalent_under',
  'disagrees_with',
  'supersedes',
]);

type GraphApiSnapshot = Omit<EvidenceGraphSnapshotResponse, 'paper'>;

export type GraphSnapshotErrorCode =
  | 'DATABASE_UNAVAILABLE'
  | 'GRAPH_API_INVALID_RESPONSE'
  | 'GRAPH_API_RESPONSE_TOO_LARGE'
  | 'GRAPH_API_UNAVAILABLE';

export class GraphSnapshotWorkflowError extends Error {
  constructor(
    readonly code: GraphSnapshotErrorCode,
    readonly status: number,
  ) {
    super(code);
    this.name = 'GraphSnapshotWorkflowError';
  }
}

export class D1GraphSnapshotRepository {
  constructor(private readonly database: D1Database) {}

  async latestOwnedPaper(
    workspaceId: string,
    ownerUserId: string,
  ): Promise<PersistedPaperSummary | null> {
    const row = await this.database
      .prepare(
        `SELECT p.arxiv_id AS paper_id, p.version, p.title, p.source_url,
                p.source_sha256, p.updated_at
         FROM papers AS p
         JOIN workspaces AS w ON w.id = p.workspace_id
         WHERE p.workspace_id = ? AND w.owner_user_id = ?
           AND p.status = 'ready' AND p.version IS NOT NULL
         ORDER BY p.updated_at DESC, p.id ASC
         LIMIT 1`,
      )
      .bind(workspaceId, ownerUserId)
      .first<Record<string, unknown>>();
    return row ? parsePersistedPaper(row) : null;
  }
}

export class HttpGraphSnapshotClient {
  constructor(
    private readonly configuration: GraphApiConfiguration,
    private readonly fetchImplementation: typeof fetch = fetch,
  ) {}

  async load(
    workspaceId: string,
    paper: PersistedPaperSummary,
  ): Promise<GraphApiSnapshot> {
    let response: Response;
    try {
      response = await this.fetchImplementation(
        new URL('/v1/graphs/snapshot', this.configuration.baseUrl),
        {
          method: 'POST',
          headers: {
            authorization: `Bearer ${this.configuration.serviceToken}`,
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            workspace_id: workspaceId,
            paper_id: paper.paper_id,
            version: paper.version,
          }),
          redirect: 'manual',
          signal: AbortSignal.timeout(20_000),
        },
      );
    } catch (error) {
      logGraphFailure(error);
      throw new GraphSnapshotWorkflowError('GRAPH_API_UNAVAILABLE', 502);
    }
    if (!response.ok) {
      void response.body?.cancel();
      throw new GraphSnapshotWorkflowError('GRAPH_API_UNAVAILABLE', 502);
    }
    try {
      assertDeclaredLengthWithinLimit(response.headers, MAX_GRAPH_RESPONSE_BYTES);
      const responseText = await readTextLimited(
        response.body,
        MAX_GRAPH_RESPONSE_BYTES,
      );
      return parseGraphApiSnapshot(JSON.parse(responseText), paper);
    } catch (error) {
      if (error instanceof PayloadTooLargeError) {
        throw new GraphSnapshotWorkflowError(
          'GRAPH_API_RESPONSE_TOO_LARGE',
          502,
        );
      }
      throw new GraphSnapshotWorkflowError('GRAPH_API_INVALID_RESPONSE', 502);
    }
  }
}

export function emptyGraphSnapshot(): EvidenceGraphSnapshotResponse {
  return { paper: null, nodes: [], edges: [], truncated: false };
}

function parsePersistedPaper(value: Record<string, unknown>): PersistedPaperSummary {
  const paperId = boundedString(value.paper_id, 32);
  if (!ARXIV_ID.test(paperId)) throw new TypeError('Invalid persisted paper ID.');
  const version = positiveInteger(value.version, 1_000_000);
  const sourceUrl = normalizeArxivHtmlUrl(boundedString(value.source_url, 2_048));
  if (!sourceUrl.endsWith(`v${version}`)) {
    throw new TypeError('Persisted paper URL is not pinned to its version.');
  }
  const sourceSha256 = boundedString(value.source_sha256, 64);
  if (!SHA256.test(sourceSha256)) throw new TypeError('Invalid persisted source hash.');
  return {
    paper_id: paperId,
    version,
    title: boundedString(value.title, 2_000),
    source_url: sourceUrl,
    source_sha256: sourceSha256,
    updated_at: nonNegativeInteger(value.updated_at, Number.MAX_SAFE_INTEGER),
  };
}

function parseGraphApiSnapshot(
  value: unknown,
  paper: PersistedPaperSummary,
): GraphApiSnapshot {
  const root = record(value);
  const nodes = boundedArray(root.nodes, MAX_GRAPH_NODES).map((item) =>
    parseNode(item, paper),
  );
  const nodeIds = new Set(nodes.map((node) => node.uuid));
  if (nodeIds.size !== nodes.length) throw new TypeError('Duplicate graph node.');
  const edges = boundedArray(root.edges, MAX_GRAPH_EDGES).map((item) =>
    parseEdge(item, nodeIds),
  );
  if (new Set(edges.map((edge) => edge.uuid)).size !== edges.length) {
    throw new TypeError('Duplicate graph edge.');
  }
  return { nodes, edges, truncated: booleanValue(root.truncated) };
}

function parseNode(value: unknown, paper: PersistedPaperSummary): EvidenceGraphNode {
  const node = record(value);
  const uuid = boundedString(node.uuid, 200);
  if (!NODE_UUID.test(uuid)) throw new TypeError('Invalid graph node UUID.');
  const kind = enumValue(node.kind, ENTITY_TYPES);
  const paperId = boundedString(node.paper_id, 32);
  if (paperId !== paper.paper_id) throw new TypeError('Cross-paper graph node.');
  const version = node.version === null ? null : positiveInteger(node.version, 1_000_000);
  const validVersion =
    kind === 'Paper'
      ? version === null
      : kind === 'PaperVersion'
        ? version !== null && version <= paper.version
        : version === paper.version;
  if (!validVersion) {
    throw new TypeError('Cross-version graph node.');
  }
  return {
    uuid,
    kind,
    logical_id: boundedString(node.logical_id, 500),
    paper_id: paperId,
    version,
    valid_at: nullableTimestamp(node.valid_at),
    verification_status: enumValue(node.verification_status, VERIFICATION_STATUSES),
    payload: record(node.payload),
    episode_uuids: stringArray(node.episode_uuids, 200, 2_000),
  };
}

function parseEdge(value: unknown, nodeIds: ReadonlySet<string>): EvidenceGraphEdge {
  const edge = record(value);
  const sourceUuid = boundedString(edge.source_uuid, 200);
  const targetUuid = boundedString(edge.target_uuid, 200);
  if (!nodeIds.has(sourceUuid) || !nodeIds.has(targetUuid)) {
    throw new TypeError('Dangling graph edge.');
  }
  return {
    uuid: boundedString(edge.uuid, 500),
    source_uuid: sourceUuid,
    target_uuid: targetUuid,
    relation: enumValue(edge.relation, RELATION_TYPES),
    source_anchor: boundedString(edge.source_anchor, 500, true),
    episode_uuids: stringArray(edge.episode_uuids, 200, 2_000),
    valid_at: nullableTimestamp(edge.valid_at),
  };
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('Expected an object.');
  }
  return value as Record<string, unknown>;
}

function boundedArray(value: unknown, maxItems: number): unknown[] {
  if (!Array.isArray(value) || value.length > maxItems) {
    throw new TypeError('Invalid array.');
  }
  return value;
}

function boundedString(
  value: unknown,
  maxLength: number,
  allowEmpty = false,
): string {
  if (
    typeof value !== 'string' ||
    value.length > maxLength ||
    (!allowEmpty && value.trim().length === 0)
  ) {
    throw new TypeError('Invalid string.');
  }
  return value;
}

function stringArray(value: unknown, maxLength: number, maxItems: number): string[] {
  return boundedArray(value, maxItems).map((item) => boundedString(item, maxLength));
}

function enumValue<T extends string>(value: unknown, allowed: ReadonlySet<T>): T {
  const parsed = boundedString(value, 100) as T;
  if (!allowed.has(parsed)) throw new TypeError('Invalid enum value.');
  return parsed;
}

function positiveInteger(value: unknown, maximum: number): number {
  const parsed = nonNegativeInteger(value, maximum);
  if (parsed < 1) throw new TypeError('Invalid positive integer.');
  return parsed;
}

function nonNegativeInteger(value: unknown, maximum: number): number {
  if (
    typeof value !== 'number' ||
    !Number.isSafeInteger(value) ||
    value < 0 ||
    value > maximum
  ) {
    throw new TypeError('Invalid integer.');
  }
  return value;
}

function nullableTimestamp(value: unknown): string | null {
  if (value === null) return null;
  const timestamp = boundedString(value, 64);
  if (
    !/(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp) ||
    Number.isNaN(Date.parse(timestamp))
  ) {
    throw new TypeError('Invalid graph timestamp.');
  }
  return timestamp;
}

function booleanValue(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new TypeError('Invalid boolean.');
  return value;
}

function logGraphFailure(error: unknown): void {
  if (error instanceof Error) {
    console.error('Graph API snapshot request failed.', {
      name: error.name,
      message: error.message,
    });
    return;
  }
  console.error('Graph API snapshot failed with a non-Error value.');
}
