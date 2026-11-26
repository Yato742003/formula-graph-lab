import { createHash } from 'node:crypto';

const REDACTED_KEYS = new Set([
  'authorization',
  'cookie',
  'token',
  'secret',
  'password',
  'api_key',
  'apikey',
  'notes',
  'rationale',
  'prompt',
  'body',
  'html',
  'latex',
  'texsource',
  'sourcefragments',
  'researchquestion',
  'formulab',
  'steps',
  'url',
  'canonicalurl',
]);

const BEARER_PATTERN = /Bearer\s+[A-Za-z0-9_.-]+/gi;
const FGL1_PATTERN = /\bfgl1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g;

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function newBffRequestId(): string {
  return crypto.randomUUID();
}

export function safeUpstreamRequestId(headers?: Headers): string | undefined {
  const value = headers?.get('x-request-id')?.trim();
  return value && UUID_PATTERN.test(value) ? value : undefined;
}

export function stableTraceFingerprint(value: string): string {
  return createHash('sha256').update(value).digest('hex').slice(0, 16);
}

function sanitizeTelemetryValue(key: string, value: unknown, depth = 0): unknown {
  const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, '');
  if (REDACTED_KEYS.has(normalized) || depth > 4) {
    return '[REDACTED]';
  }
  if (Array.isArray(value)) {
    return value.slice(0, 20).map((item) => sanitizeTelemetryValue('', item, depth + 1));
  }
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [childKey, childValue] of Object.entries(value)) {
      if (childValue !== undefined) {
        out[childKey] = sanitizeTelemetryValue(childKey, childValue, depth + 1);
      }
    }
    return out;
  }
  if (typeof value === 'string') {
    return value
      .replace(BEARER_PATTERN, 'Bearer [REDACTED]')
      .replace(FGL1_PATTERN, 'fgl1.[REDACTED]');
  }
  return value;
}

export function emitBffTelemetry(
  event: string,
  fields: Record<string, unknown>,
): void {
  const sanitized = sanitizeTelemetryValue('', {
    event,
    ts: new Date().toISOString(),
    ...fields,
  });
  const line = JSON.stringify(sanitized);
  const status = typeof fields.status === 'number' ? fields.status : 200;
  if (status >= 500 || fields.severity === 'warning' || fields.severity === 'error') {
    console.warn(line);
  } else {
    console.info(line);
  }
}
