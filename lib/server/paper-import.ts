import type {
  EvidenceImportReceipt,
  ImportedEquation,
  ImportedPaper,
  ImportedSection,
  WorkspaceImportResponse,
} from '../import-types';
import { normalizeArxivHtmlUrl } from '../paper-url';
import {
  assertDeclaredLengthWithinLimit,
  PayloadTooLargeError,
  readTextLimited,
} from './limited-stream';

const MAX_GRAPH_RESPONSE_BYTES = 2 * 1024 * 1024;
const MAX_SECTIONS = 2_000;
const MAX_EQUATIONS = 10_000;
const ARXIV_ID = /^\d{4}\.\d{4,5}$/;
const SHA256 = /^[a-f0-9]{64}$/;
const EXTRACTION_METHODS = new Set<ImportedEquation['extraction_method']>([
  'tex_annotation',
  'alttext',
  'mathml_text',
  'assembled_tex',
]);

export type ImportErrorCode =
  | 'DATABASE_UNAVAILABLE'
  | 'EXTRACTION_FAILED'
  | 'GRAPH_API_INVALID_RESPONSE'
  | 'GRAPH_API_NOT_CONFIGURED'
  | 'GRAPH_API_RESPONSE_TOO_LARGE'
  | 'GRAPH_API_UNAVAILABLE'
  | 'WORKSPACE_OWNERSHIP_CONFLICT';

export class ImportWorkflowError extends Error {
  constructor(
    readonly code: ImportErrorCode,
    readonly status: number,
  ) {
    super(code);
    this.name = 'ImportWorkflowError';
  }
}

export type GraphApiConfiguration = {
  baseUrl: URL;
  serviceToken: string;
};

export function resolveGraphApiConfiguration(
  values: Record<string, string | undefined>,
  production: boolean,
): GraphApiConfiguration | null {
  const rawBase =
    values.GRAPH_API_URL ??
    (production ? undefined : 'http://127.0.0.1:8000');
  const serviceToken = values.GRAPH_API_SERVICE_TOKEN?.trim();
  if (!rawBase || !serviceToken || serviceToken.length > 4_096) return null;

  let baseUrl: URL;
  try {
    baseUrl = new URL(rawBase);
  } catch {
    return null;
  }
  if (
    baseUrl.username ||
    baseUrl.password ||
    baseUrl.search ||
    baseUrl.hash ||
    !['', '/'].includes(baseUrl.pathname)
  ) {
    return null;
  }
  if (production && baseUrl.protocol !== 'https:') return null;
  if (!production && baseUrl.protocol === 'http:') {
    const loopback = new Set(['127.0.0.1', 'localhost', '[::1]']);
    if (!loopback.has(baseUrl.hostname)) return null;
  } else if (baseUrl.protocol !== 'https:') {
    return null;
  }

  return { baseUrl, serviceToken };
}

export interface GraphImportClient {
  importEvidence(
    canonicalUrl: string,
    workspaceId: string,
  ): Promise<{ paper: ImportedPaper; receipt: EvidenceImportReceipt }>;
}

export class HttpGraphImportClient implements GraphImportClient {
  constructor(
    private readonly configuration: GraphApiConfiguration,
    private readonly fetchImplementation: typeof fetch = fetch,
  ) {}

  async importEvidence(canonicalUrl: string, workspaceId: string) {
    let response: Response;
    try {
      response = await this.fetchImplementation(
        new URL('/v1/imports', this.configuration.baseUrl),
        {
          method: 'POST',
          headers: {
            authorization: `Bearer ${this.configuration.serviceToken}`,
            'content-type': 'application/json',
          },
          body: JSON.stringify({ url: canonicalUrl, workspace_id: workspaceId }),
          // Cloudflare Workers supports manual redirects, not `error`.
          // Keeping redirects manual prevents the bearer token from being
          // forwarded to an unexpected origin.
          redirect: 'manual',
          signal: AbortSignal.timeout(20_000),
        },
      );
    } catch (error) {
      logGraphClientFailure(error);
      throw new ImportWorkflowError('GRAPH_API_UNAVAILABLE', 502);
    }

    if (!response.ok) {
      void response.body?.cancel();
      throw new ImportWorkflowError(
        response.status >= 400 && response.status < 500
          ? 'EXTRACTION_FAILED'
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
      return parseGraphImportResponse(JSON.parse(responseText));
    } catch (error) {
      if (error instanceof PayloadTooLargeError) {
        throw new ImportWorkflowError('GRAPH_API_RESPONSE_TOO_LARGE', 502);
      }
      throw new ImportWorkflowError('GRAPH_API_INVALID_RESPONSE', 502);
    }
  }
}

export type PersistedPaper = {
  id: string;
  workspaceId: string;
  arxivId: string;
  version: number;
  title: string;
  sourceUrl: string;
  sourceSha256: string;
  now: number;
};

export interface ImportRepository {
  ensureWorkspace(input: {
    id: string;
    ownerUserId: string;
    now: number;
  }): Promise<void>;
  createJob(input: {
    id: string;
    workspaceId: string;
    requestedBy: string;
    canonicalUrl: string;
    now: number;
  }): Promise<void>;
  markSucceeded(input: {
    jobId: string;
    requestedBy: string;
    paper: PersistedPaper;
  }): Promise<void>;
  markFailed(input: {
    jobId: string;
    workspaceId: string;
    requestedBy: string;
    errorCode: ImportErrorCode;
    now: number;
  }): Promise<void>;
}

export class D1ImportRepository implements ImportRepository {
  constructor(private readonly database: D1Database) {}

  async ensureWorkspace(input: {
    id: string;
    ownerUserId: string;
    now: number;
  }): Promise<void> {
    const inserted = await this.database
      .prepare(
        `INSERT INTO workspaces
           (id, owner_user_id, name, created_at, updated_at)
         VALUES (?, ?, 'Personal research lab', ?, ?)
         ON CONFLICT(id) DO NOTHING`,
      )
      .bind(input.id, input.ownerUserId, input.now, input.now)
      .run();
    if (!inserted.success) throw databaseUnavailable();

    const owned = await this.database
      .prepare(
        `SELECT id FROM workspaces
         WHERE id = ? AND owner_user_id = ?`,
      )
      .bind(input.id, input.ownerUserId)
      .first<{ id: string }>();
    if (!owned) {
      throw new ImportWorkflowError('WORKSPACE_OWNERSHIP_CONFLICT', 409);
    }
  }

  async createJob(input: {
    id: string;
    workspaceId: string;
    requestedBy: string;
    canonicalUrl: string;
    now: number;
  }): Promise<void> {
    const result = await this.database
      .prepare(
        `INSERT INTO import_jobs
           (id, workspace_id, paper_id, requested_by, canonical_url, status,
            error_code, created_at, finished_at)
         SELECT ?, id, NULL, ?, ?, 'running', NULL, ?, NULL
         FROM workspaces
         WHERE id = ? AND owner_user_id = ?`,
      )
      .bind(
        input.id,
        input.requestedBy,
        input.canonicalUrl,
        input.now,
        input.workspaceId,
        input.requestedBy,
      )
      .run();
    requireOneChangedRow(result);
  }

  async markSucceeded(input: {
    jobId: string;
    requestedBy: string;
    paper: PersistedPaper;
  }): Promise<void> {
    const paper = input.paper;
    const paperStatement = this.database
      .prepare(
        `INSERT INTO papers
           (id, workspace_id, arxiv_id, version, title, source_url,
            source_sha256, status, created_at, updated_at)
         SELECT ?, id, ?, ?, ?, ?, ?, 'ready', ?, ?
         FROM workspaces
         WHERE id = ? AND owner_user_id = ?
         ON CONFLICT(id) DO UPDATE SET
           title = excluded.title,
           source_url = excluded.source_url,
           status = 'ready',
           updated_at = excluded.updated_at
         WHERE papers.workspace_id = excluded.workspace_id
           AND papers.source_sha256 = excluded.source_sha256`,
      )
      .bind(
        paper.id,
        paper.arxivId,
        paper.version,
        paper.title,
        paper.sourceUrl,
        paper.sourceSha256,
        paper.now,
        paper.now,
        paper.workspaceId,
        input.requestedBy,
      );
    const jobStatement = this.database
      .prepare(
        `UPDATE import_jobs
         SET paper_id = ?, status = 'succeeded', error_code = NULL,
             finished_at = ?
         WHERE id = ? AND workspace_id = ? AND requested_by = ?
           AND status IN ('queued', 'running')`,
      )
      .bind(
        paper.id,
        paper.now,
        input.jobId,
        paper.workspaceId,
        input.requestedBy,
      );

    const results = await this.database.batch([paperStatement, jobStatement]);
    for (const result of results) requireOneChangedRow(result);
  }

  async markFailed(input: {
    jobId: string;
    workspaceId: string;
    requestedBy: string;
    errorCode: ImportErrorCode;
    now: number;
  }): Promise<void> {
    const result = await this.database
      .prepare(
        `UPDATE import_jobs
         SET status = 'failed', error_code = ?, finished_at = ?
         WHERE id = ? AND workspace_id = ? AND requested_by = ?
           AND status IN ('queued', 'running')`,
      )
      .bind(
        input.errorCode,
        input.now,
        input.jobId,
        input.workspaceId,
        input.requestedBy,
      )
      .run();
    requireOneChangedRow(result);
  }
}

export async function runPaperImport(input: {
  canonicalUrl: string;
  userId: string;
  repository: ImportRepository;
  graphClient: GraphImportClient;
  now?: () => number;
  createId?: () => string;
}): Promise<WorkspaceImportResponse> {
  const now = input.now ?? Date.now;
  const createId = input.createId ?? (() => crypto.randomUUID());
  const workspaceId = await stableIdentifier('ws', input.userId);
  const startedAt = now();

  try {
    await input.repository.ensureWorkspace({
      id: workspaceId,
      ownerUserId: input.userId,
      now: startedAt,
    });
  } catch (error) {
    if (error instanceof ImportWorkflowError) throw error;
    throw databaseUnavailable();
  }

  const jobId = `job_${createId()}`;
  try {
    await input.repository.createJob({
      id: jobId,
      workspaceId,
      requestedBy: input.userId,
      canonicalUrl: input.canonicalUrl,
      now: startedAt,
    });
  } catch {
    throw databaseUnavailable();
  }

  try {
    const imported = await input.graphClient.importEvidence(
      input.canonicalUrl,
      workspaceId,
    );
    const paperId = await stableIdentifier(
      'paper',
      `${workspaceId}\u0000${imported.paper.paper_id}\u0000v${imported.paper.version}`,
    );
    await input.repository.markSucceeded({
      jobId,
      requestedBy: input.userId,
      paper: {
        id: paperId,
        workspaceId,
        arxivId: imported.paper.paper_id,
        version: imported.paper.version ?? 0,
        title: imported.paper.title,
        sourceUrl: imported.paper.source_url,
        sourceSha256: imported.paper.source_sha256,
        now: now(),
      },
    });
    return { job_id: jobId, ...imported };
  } catch (error) {
    const failure =
      error instanceof ImportWorkflowError ? error : databaseUnavailable();
    try {
      await input.repository.markFailed({
        jobId,
        workspaceId,
        requestedBy: input.userId,
        errorCode: failure.code,
        now: now(),
      });
    } catch {
      throw databaseUnavailable();
    }
    throw failure;
  }
}

function parseGraphImportResponse(value: unknown): {
  paper: ImportedPaper;
  receipt: EvidenceImportReceipt;
} {
  const root = record(value);
  return {
    paper: parsePaper(root.paper),
    receipt: parseReceipt(root.receipt),
  };
}

function parsePaper(value: unknown): ImportedPaper {
  const paper = record(value);
  const paperId = boundedString(paper.paper_id, 32);
  if (!ARXIV_ID.test(paperId)) throw new TypeError('Invalid paper id.');
  const version = nullablePositiveInteger(paper.version);
  if (version === null) throw new TypeError('A pinned paper version is required.');
  const sourceUrl = normalizeArxivHtmlUrl(
    boundedString(paper.source_url, 2_048),
  );
  if (!sourceUrl.endsWith(`v${version}`)) {
    throw new TypeError('Paper source is not pinned to its declared version.');
  }
  const sourceSha256 = boundedString(paper.source_sha256, 64);
  if (!SHA256.test(sourceSha256)) throw new TypeError('Invalid source hash.');

  return {
    paper_id: paperId,
    version,
    title: boundedString(paper.title, 2_000),
    authors: stringArray(paper.authors, 500, 200),
    version_published_at: nullableDateString(paper.version_published_at),
    metadata_warnings: stringArray(paper.metadata_warnings, 2_000, 100),
    source_url: sourceUrl,
    source_sha256: sourceSha256,
    sections: boundedArray(paper.sections, MAX_SECTIONS).map(parseSection),
    equations: boundedArray(paper.equations, MAX_EQUATIONS).map(parseEquation),
  };
}

function parseSection(value: unknown): ImportedSection {
  const section = record(value);
  return {
    section_id: boundedString(section.section_id, 300),
    anchor: boundedString(section.anchor, 300),
    anchor_is_source: booleanValue(section.anchor_is_source),
    title: boundedString(section.title, 2_000),
    order: positiveInteger(section.order),
    parent_section_id: nullableString(section.parent_section_id, 300),
    text: boundedString(section.text, 200_000, true),
    equation_ids: stringArray(section.equation_ids, 300, MAX_EQUATIONS),
  };
}

function parseEquation(value: unknown): ImportedEquation {
  const equation = record(value);
  const method = boundedString(equation.extraction_method, 40);
  if (!EXTRACTION_METHODS.has(method as ImportedEquation['extraction_method'])) {
    throw new TypeError('Invalid extraction method.');
  }
  const confidence = numberValue(equation.confidence);
  if (confidence < 0 || confidence > 1) {
    throw new TypeError('Invalid extraction confidence.');
  }
  return {
    equation_id: boundedString(equation.equation_id, 300),
    anchor: boundedString(equation.anchor, 300),
    anchor_is_source: booleanValue(equation.anchor_is_source),
    latex: boundedString(equation.latex, 200_000),
    source_fragments: stringArray(equation.source_fragments, 200_000, 100),
    warnings: stringArray(equation.warnings, 2_000, 100),
    equation_number: nullableString(equation.equation_number, 100),
    section: nullableString(equation.section, 2_000),
    section_id: nullableString(equation.section_id, 300),
    preceding_text: nullableString(equation.preceding_text, 100_000),
    following_text: nullableString(equation.following_text, 100_000),
    extraction_method: method as ImportedEquation['extraction_method'],
    confidence,
  };
}

function parseReceipt(value: unknown): EvidenceImportReceipt {
  const receipt = record(value);
  return {
    import_uuid: boundedString(receipt.import_uuid, 200),
    node_count: nonNegativeInteger(receipt.node_count),
    edge_count: nonNegativeInteger(receipt.edge_count),
    episode_count: positiveInteger(receipt.episode_count),
    replayed: booleanValue(receipt.replayed),
  };
}

async function stableIdentifier(prefix: string, value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  const hex = Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${prefix}_${hex.slice(0, 48)}`;
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

function nullableString(value: unknown, maxLength: number): string | null {
  return value === null ? null : boundedString(value, maxLength, true);
}

function stringArray(
  value: unknown,
  maxStringLength: number,
  maxItems: number,
): string[] {
  return boundedArray(value, maxItems).map((item) =>
    boundedString(item, maxStringLength, true),
  );
}

function booleanValue(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new TypeError('Invalid boolean.');
  return value;
}

function numberValue(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new TypeError('Invalid number.');
  }
  return value;
}

function nonNegativeInteger(value: unknown): number {
  const number = numberValue(value);
  if (!Number.isInteger(number) || number < 0) {
    throw new TypeError('Invalid non-negative integer.');
  }
  return number;
}

function positiveInteger(value: unknown): number {
  const number = nonNegativeInteger(value);
  if (number < 1) throw new TypeError('Invalid positive integer.');
  return number;
}

function nullablePositiveInteger(value: unknown): number | null {
  return value === null ? null : positiveInteger(value);
}

function nullableDateString(value: unknown): string | null {
  if (value === null) return null;
  const date = boundedString(value, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new TypeError('Invalid date.');
  }
  return date;
}

function requireOneChangedRow(result: D1Result<unknown>): void {
  if (!result.success || Number(result.meta.changes) !== 1) {
    throw databaseUnavailable();
  }
}

function databaseUnavailable(): ImportWorkflowError {
  return new ImportWorkflowError('DATABASE_UNAVAILABLE', 503);
}

function logGraphClientFailure(error: unknown): void {
  if (error instanceof Error) {
    console.error('Graph API request failed.', {
      name: error.name,
      message: error.message,
    });
    return;
  }
  console.error('Graph API request failed with a non-Error value.');
}
