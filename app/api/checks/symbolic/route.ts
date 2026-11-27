import { equationUuid, inspectorRequest } from '@/lib/server/inspector-request';

const ALLOWED_KEYS = new Set([
  'target_uuid',
  'formula_b',
  'steps',
  'include_lean_export',
  'allow_auto_inferred',
  'timeout_ms',
]);

export function POST(request: Request) {
  return inspectorRequest(request, '/v1/checks/symbolic/quick', 'bff_quick_symbolic_check', 16 * 1024, data => {
    const { target_uuid, formula_b, steps, include_lean_export, allow_auto_inferred, timeout_ms } = data;
    if (Object.keys(data).some(k => !ALLOWED_KEYS.has(k))
      || typeof target_uuid !== 'string' || !equationUuid.test(target_uuid)) {
      throw new Error('Invalid check input');
    }
    const hasFormulaB = formula_b !== undefined;
    const hasSteps = steps !== undefined;
    if (!hasFormulaB && !hasSteps) {
      throw new Error('Invalid check input');
    }
    if (hasFormulaB && (typeof formula_b !== 'string' || !formula_b.trim() || formula_b.length > 2048)) {
      throw new Error('Invalid check input');
    }
    if (hasSteps) {
      if (!Array.isArray(steps) || steps.length < 1 || steps.length > 8
        || steps.some(step => typeof step !== 'string' || !step.trim() || step.length > 2048)) {
        throw new Error('Invalid check input');
      }
      if (hasFormulaB && (steps[steps.length - 1] as string).trim() !== (formula_b as string).trim()) {
        throw new Error('Invalid check input');
      }
    }
    if (include_lean_export !== undefined && typeof include_lean_export !== 'boolean') {
      throw new Error('Invalid check input');
    }
    if (allow_auto_inferred !== undefined && typeof allow_auto_inferred !== 'boolean') {
      throw new Error('Invalid check input');
    }
    if (allow_auto_inferred === true && include_lean_export === true) {
      throw new Error('Invalid check input');
    }
    if (timeout_ms !== undefined && (typeof timeout_ms !== 'number' || !Number.isInteger(timeout_ms)
      || timeout_ms < 500 || timeout_ms > 10000)) {
      throw new Error('Invalid check input');
    }
    const payload: Record<string, unknown> = { target_uuid };
    if (hasFormulaB) payload.formula_b = formula_b;
    if (hasSteps) payload.steps = steps;
    if (include_lean_export !== undefined) payload.include_lean_export = include_lean_export;
    if (allow_auto_inferred !== undefined) payload.allow_auto_inferred = allow_auto_inferred;
    if (timeout_ms !== undefined) payload.timeout_ms = timeout_ms;
    return payload;
  });
}
