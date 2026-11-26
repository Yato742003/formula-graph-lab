// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import QuickVerifyPanel from '../components/quick-verify-panel';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('sends only the proposed rewrite and opens review on a blocked verdict', async () => {
  const review = vi.fn();
  vi.stubGlobal('fetch', async (_: string, init: RequestInit) => {
    expect(JSON.parse(init.body as string)).toEqual({ target_uuid: 'eq', formula_b: 'x+x' });
    return Response.json({
      outcome: 'unknown',
      reason_code: 'UNRESOLVED_SHAPE',
      missing_symbols: ['x'],
      scope: 'ad_hoc_check_not_frozen_campaign',
    });
  });
  render(<QuickVerifyPanel equation="eq" onReview={review} />);
  fireEvent.change(screen.getByLabelText('Proposed rewrite'), { target: { value: 'x+x' } });
  fireEvent.click(screen.getByText('Verify in Sandbox'));
  fireEvent.click(await screen.findByText('Open Batch Review'));
  expect(review).toHaveBeenCalledOnce();
  expect(screen.getByText('ad-hoc check · not frozen campaign')).toBeTruthy();
});

it('runs multi-step derivation chains and renders domain discharge + Lean 4 five-bin audit', async () => {
  const review = vi.fn();
  vi.stubGlobal('fetch', async (_: string, init: RequestInit) => {
    expect(JSON.parse(init.body as string)).toEqual({
      target_uuid: 'eq-chain',
      steps: ['x + (a + b) - b', 'x + a'],
      include_lean_export: true,
    });
    return Response.json({
      outcome: 'supported',
      scope: 'ad_hoc_check_not_frozen_campaign',
      duration_ms: 412,
      failed_step_index: null,
      applied_assumptions: ['x:scalar:positive', 'a:scalar:real'],
      domain_obligations: [
        {
          obligation_id: 'ob-1',
          predicate: 'non_negative',
          expression: 'x**2',
          location: 'root.sqrt_argument',
          status: 'discharged',
          discharged_by: ['sympy_discharge_1234'],
          conditions: [],
        },
      ],
      steps: [
        {
          step_index: 1,
          from_formula: '\\sqrt{x^2} + (a + b) - b',
          to_formula: 'x + (a + b) - b',
          outcome: 'supported',
          duration_ms: 210,
        },
        {
          step_index: 2,
          from_formula: 'x + (a + b) - b',
          to_formula: 'x + a',
          outcome: 'supported',
          duration_ms: 202,
        },
      ],
      lean_export: {
        status: 'audited_draft',
        theorem_name: 'fgl_chain_abcd1234',
        lean_code: 'theorem fgl_chain_abcd1234 (x : ℝ) (h_x_pos : 0 < x) : Real.sqrt (x ^ 2) = x := by\n  sorry\n',
        audit: {
          declaration_fidelity: 'PASS',
          draft_allowlist: 'PASS',
          proof_status: 'FROZEN_UNPROVED',
          kernel_checked: false,
          sorry_count: 1,
          binder_table: [
            { name: 'x', type_expr: 'ℝ', bin: 'TYPING', citation: 'review:r1:x' },
            { name: 'h_x_pos', type_expr: '0 < x', bin: 'SOURCE', citation: 'review:r1:x:positive' },
          ],
          bin_counts: { SOURCE: 1, STANDING: 0, TYPING: 1, RULED: 0, EXCESS: 0 },
          failure_reasons: [],
        },
      },
    });
  });

  render(<QuickVerifyPanel equation="eq-chain" onReview={review} />);
  fireEvent.click(screen.getByText('Derivation chain'));
  fireEvent.change(screen.getByLabelText('Step 1'), { target: { value: 'x + (a + b) - b' } });
  fireEvent.change(screen.getByLabelText('Step 2'), { target: { value: 'x + a' } });
  fireEvent.click(screen.getByLabelText('Export audited Lean 4 statement'));
  fireEvent.click(screen.getByText('Verify Chain in Sandbox'));

  expect(await screen.findByText('Step 1 · supported')).toBeTruthy();
  expect(screen.getByText('Step 2 · supported')).toBeTruthy();
  expect(screen.getByText('discharged')).toBeTruthy();
  expect(screen.getByText('DECLARATION_FIDELITY: PASS')).toBeTruthy();
  expect(screen.getByText('DRAFT_ALLOWLIST: PASS')).toBeTruthy();
  expect(screen.getByText('FROZEN_UNPROVED')).toBeTruthy();
});
