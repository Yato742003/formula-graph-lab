export class PayloadTooLargeError extends Error {
  constructor() {
    super('Payload exceeds the configured byte limit.');
    this.name = 'PayloadTooLargeError';
  }
}

export function assertDeclaredLengthWithinLimit(
  headers: Headers,
  maxBytes: number,
): void {
  const rawLength = headers.get('content-length');
  if (!rawLength) return;

  const declaredLength = Number(rawLength);
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    throw new PayloadTooLargeError();
  }
}

export async function readTextLimited(
  body: ReadableStream<Uint8Array> | null,
  maxBytes: number,
): Promise<string> {
  if (!body) return '';

  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let byteCount = 0;
  let text = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      byteCount += value.byteLength;
      if (byteCount > maxBytes) {
        await reader.cancel();
        throw new PayloadTooLargeError();
      }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
    return text;
  } finally {
    reader.releaseLock();
  }
}
