import type { GraphApiConfiguration } from './paper-import';
import {
  assertDeclaredLengthWithinLimit,
  PayloadTooLargeError,
  readTextLimited,
} from './limited-stream';

const MAX_RESPONSE_BYTES = 64 * 1024;
const UUID =
  /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const SYMBOL_CATEGORIES = new Set([
  'scalar',
  'vector',
  'matrix',
  'tensor',
  'function',
  'distribution',
  'index',
]);
const SYMBOL_DOMAINS = new Set([
  'real',
  'positive',
  'non_negative',
  'complex',
  'integer',
]);
const INPUT_FIELDS = new Set([
  'equation_uuid',
  'symbol_name',
  'decision',
  'reviewed_contract',
  'evidence',
]);
const CONTRACT_FIELDS = new Set([
  'name',
  'category',
  'shape',
  'feature_rank',
  'domain',
  'constraints',
  'scope',
  'normalization',
  'mask',
  'causal',
  'resource_class',
]);

export type ReviewedContractInput = {
  name: string;
  category: string;
  shape: Array<number | string> | null;
  feature_rank?: number | null;
  domain: string;
  constraints: string[];
  scope?: string;
  normalization?: string;
  mask?: string;
  causal?: boolean | null;
  resource_class?: 'unknown' | 'not_applicable' | 'cpu' | 'gpu';
};

export type ContractReviewInput = {
  equation_uuid: string;
  symbol_name: string;
  decision: 'accepted' | 'rejected';
  reviewed_contract: ReviewedContractInput | null;
  evidence: string[];
};

export type ContractReviewResult = ContractReviewInput & {
  review_id: string;
  workspace_id: string;
  reviewer_id: string;
  reviewer_role: 'researcher' | 'reviewer' | 'admin';
  schema_version: 'contract-review.v1';
  reviewed_at: string;
  replayed: boolean;
};

export type ContractReviewErrorCode =
  | 'GRAPH_API_INVALID_RESPONSE'
  | 'GRAPH_API_RESPONSE_TOO_LARGE'
  | 'GRAPH_API_UNAVAILABLE'
  | 'REVIEW_REJECTED';

export class ContractReviewWorkflowError extends Error {
  constructor(
    readonly code: ContractReviewErrorCode,
    readonly status: number,
  ) {
    super(code);
    this.name = 'ContractReviewWorkflowError';
  }
}

export function parseContractReviewInput(value: unknown): ContractReviewInput {
  const input = record(value);
  rejectUnknownFields(input, INPUT_FIELDS);
  const equationUuid = boundedString(input.equation_uuid, 200);
  if (!UUID.test(equationUuid)) throw new TypeError('Invalid equation UUID.');
  const symbolName = boundedString(input.symbol_name, 200);
  const decision = input.decision;
  if (decision !== 'accepted' && decision !== 'rejected') {
    throw new TypeError('Invalid review decision.');
  }
  const reviewedContract =
    input.reviewed_contract == null
      ? null
      : parseReviewedContract(input.reviewed_contract, symbolName);
  if (decision === 'accepted' && reviewedContract === null) {
    throw new TypeError('An accepted review requires contract values.');
  }
  return {
    equation_uuid: equationUuid,
    symbol_name: symbolName,
    decision,
    reviewed_contract: reviewedContract,
    evidence: boundedStringArray(input.evidence, 50, 500),
  };
}

export class HttpContractReviewClient {
  constructor(
    private readonly configuration: GraphApiConfiguration,
    private readonly fetchImplementation: typeof fetch = fetch,
  ) {}

  async append(
    input: ContractReviewInput,
    workspaceId: string,
    actorId: string,
    idempotencyKey: string,
  ): Promise<ContractReviewResult> {
    if (!actorId || actorId.length > 200 || !/^[\x20-\x7e]+$/.test(actorId)) {
      throw new TypeError('Invalid authenticated actor.');
    }
    if (
      idempotencyKey.length < 16 ||
      idempotencyKey.length > 200 ||
      !/^[\x20-\x7e]+$/.test(idempotencyKey)
    ) {
      throw new TypeError('Invalid idempotency key.');
    }
    let response: Response;
    try {
      response = await this.fetchImplementation(
        new URL('/v1/contract-reviews', this.configuration.baseUrl),
        {
          method: 'POST',
          headers: {
            authorization: `Bearer ${this.configuration.serviceToken}`,
            'content-type': 'application/json',
            'idempotency-key': idempotencyKey,
            'x-fgl-actor-id': actorId,
            'x-fgl-actor-role': 'researcher',
          },
          body: JSON.stringify({ ...input, workspace_id: workspaceId }),
          redirect: 'manual',
          signal: AbortSignal.timeout(20_000),
        },
      );
    } catch {
      throw new ContractReviewWorkflowError('GRAPH_API_UNAVAILABLE', 502);
    }
    if (!response.ok) {
      void response.body?.cancel();
      throw new ContractReviewWorkflowError(
        response.status >= 400 && response.status < 500
          ? 'REVIEW_REJECTED'
          : 'GRAPH_API_UNAVAILABLE',
        response.status >= 400 && response.status < 500 ? 422 : 502,
      );
    }
    try {
      assertDeclaredLengthWithinLimit(response.headers, MAX_RESPONSE_BYTES);
      const responseText = await readTextLimited(
        response.body,
        MAX_RESPONSE_BYTES,
      );
      return parseContractReviewResult(JSON.parse(responseText));
    } catch (error) {
      if (error instanceof PayloadTooLargeError) {
        throw new ContractReviewWorkflowError(
          'GRAPH_API_RESPONSE_TOO_LARGE',
          502,
        );
      }
      throw new ContractReviewWorkflowError('GRAPH_API_INVALID_RESPONSE', 502);
    }
  }
}

function parseReviewedContract(
  value: unknown,
  symbolName: string,
): ReviewedContractInput {
  const contract = record(value);
  rejectUnknownFields(contract, CONTRACT_FIELDS);
  const name = boundedString(contract.name, 200);
  if (name !== symbolName)
    throw new TypeError('Reviewed symbol does not match.');
  const category = boundedString(contract.category, 32);
  if (!SYMBOL_CATEGORIES.has(category))
    throw new TypeError('Invalid category.');
  const domain = boundedString(contract.domain, 32);
  if (!SYMBOL_DOMAINS.has(domain)) throw new TypeError('Invalid domain.');
  const rawShape = contract.shape;
  if (rawShape !== null && !Array.isArray(rawShape)) {
    throw new TypeError('Invalid shape.');
  }
  const shape =
    rawShape === null
      ? null
      : rawShape.map((dimension) => {
          if (
            (typeof dimension === 'number' &&
              Number.isInteger(dimension) &&
              dimension > 0) ||
            (typeof dimension === 'string' &&
              dimension.length > 0 &&
              dimension.length <= 100)
          ) {
            return dimension;
          }
          throw new TypeError('Invalid shape dimension.');
        });
  if (shape !== null && shape.length > 16)
    throw new TypeError('Shape is too large.');
  const featureRank = contract.feature_rank;
  if (featureRank !== undefined && featureRank !== null &&
      (typeof featureRank !== 'number' || !Number.isInteger(featureRank) ||
        featureRank < 1 || featureRank > 100000 || category !== 'vector')) {
    throw new TypeError('Feature rank must be a bounded positive integer on a vector contract.');
  }
  const scope =
    contract.scope == null
      ? undefined
      : boundedString(contract.scope, 500);
  const normalization = contract.normalization;
  if (normalization !== undefined && (
    typeof normalization !== 'string' ||
    !['none', 'l1', 'l2', 'softmax', 'layer_norm', 'rms_norm', 'batch_norm', 'unknown'].includes(normalization)
  )) throw new TypeError('Invalid normalization.');
  const mask = contract.mask;
  if (mask !== undefined && (
    typeof mask !== 'string' ||
    !['none', 'causal', 'padding', 'sliding_window', 'custom', 'missing'].includes(mask)
  )) throw new TypeError('Invalid mask.');
  const causal = contract.causal;
  if (causal !== undefined && causal !== null && typeof causal !== 'boolean') {
    throw new TypeError('Invalid causality.');
  }
  const resourceClass = contract.resource_class;
  if (resourceClass !== undefined &&
      resourceClass !== 'unknown' &&
      resourceClass !== 'not_applicable' &&
      resourceClass !== 'cpu' &&
      resourceClass !== 'gpu') {
    throw new TypeError('Invalid resource class.');
  }
  return {
    name,
    category,
    shape,
    ...(featureRank !== undefined ? { feature_rank: featureRank as number | null } : {}),
    domain,
    constraints: boundedStringArray(contract.constraints, 50, 500),
    ...(scope ? { scope } : {}),
    ...(normalization !== undefined ? { normalization } : {}),
    ...(mask !== undefined ? { mask } : {}),
    ...(causal !== undefined ? { causal } : {}),
    ...(resourceClass !== undefined ? { resource_class: resourceClass } : {}),
  };
}

function parseContractReviewResult(value: unknown): ContractReviewResult {
  const result = record(value);
  const input = parseContractReviewInput({
    equation_uuid: result.equation_uuid,
    symbol_name: result.symbol_name,
    decision: result.decision,
    reviewed_contract: result.reviewed_contract,
    evidence: result.evidence,
  });
  const reviewId = boundedString(result.review_id, 200);
  if (!UUID.test(reviewId)) throw new TypeError('Invalid review UUID.');
  const reviewerRole = result.reviewer_role;
  if (!['researcher', 'reviewer', 'admin'].includes(String(reviewerRole))) {
    throw new TypeError('Invalid reviewer role.');
  }
  if (result.schema_version !== 'contract-review.v1') {
    throw new TypeError('Invalid review schema version.');
  }
  if (typeof result.replayed !== 'boolean')
    throw new TypeError('Invalid replay state.');
  const reviewedAt = boundedString(result.reviewed_at, 100);
  if (!Number.isFinite(Date.parse(reviewedAt)))
    throw new TypeError('Invalid review time.');
  return {
    ...input,
    review_id: reviewId,
    workspace_id: boundedString(result.workspace_id, 200),
    reviewer_id: boundedString(result.reviewer_id, 200),
    reviewer_role: reviewerRole as ContractReviewResult['reviewer_role'],
    schema_version: 'contract-review.v1',
    reviewed_at: reviewedAt,
    replayed: result.replayed,
  };
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('Expected an object.');
  }
  return value as Record<string, unknown>;
}

function rejectUnknownFields(
  value: Record<string, unknown>,
  allowed: ReadonlySet<string>,
) {
  if (Object.keys(value).some((field) => !allowed.has(field))) {
    throw new TypeError('Unknown field.');
  }
}

function boundedString(value: unknown, maximum: number): string {
  if (typeof value !== 'string') throw new TypeError('Expected string.');
  const normalized = value.trim();
  if (!normalized || normalized.length > maximum) {
    throw new TypeError('String is empty or too long.');
  }
  return normalized;
}

function boundedStringArray(
  value: unknown,
  maximumItems: number,
  maximumLength: number,
): string[] {
  if (!Array.isArray(value) || value.length > maximumItems) {
    throw new TypeError('Invalid string list.');
  }
  const values = value.map((item) => boundedString(item, maximumLength));
  if (new Set(values).size !== values.length)
    throw new TypeError('Duplicate value.');
  return values;
}
