import { equationUuid, inspectorRequest } from '@/lib/server/inspector-request';

export function POST(request: Request) {
  return inspectorRequest(request, '/v1/checks/symbolic/quick', 'bff_quick_symbolic_check', 16 * 1024, data => {
    const {target_uuid, formula_b, timeout_ms} = data;
    if (Object.keys(data).some(k => !['target_uuid','formula_b','timeout_ms'].includes(k))
        || typeof target_uuid !== 'string' || !equationUuid.test(target_uuid)
        || typeof formula_b !== 'string' || !formula_b.trim() || formula_b.length > 2048
        || (timeout_ms !== undefined && (typeof timeout_ms !== 'number' || !Number.isInteger(timeout_ms)
          || timeout_ms < 500 || timeout_ms > 10000))) throw new Error('Invalid check input');
    return {target_uuid, formula_b, timeout_ms};
  });
}
