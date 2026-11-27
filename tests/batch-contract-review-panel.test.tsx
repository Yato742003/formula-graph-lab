// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import BatchContractReviewPanel from '../components/batch-contract-review-panel';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('keeps presets in the draft until approval, then reloads the persisted decision', async () => {
  let approved = false;
  const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      expect(new Headers(init.headers).get('idempotency-key')).toBeTruthy();
      expect(JSON.parse(init.body as string).items[0].reviewed_contract.domain).toBe('positive');
      approved = true;
    }
    const value = {name:'x', category:'scalar', domain:'real', constraints:[], shape:[]};
    return Response.json({items:[{symbol:'x', inferred:{...value,inference_confidence:0.8},
      latest_review:approved ? {decision:'accepted', reviewed_contract:value} : null}]});
  });
  vi.stubGlobal('fetch', fetcher);
  render(<BatchContractReviewPanel equation="eq" />);
  fireEvent.click(screen.getByText('Batch Review'));
  await screen.findByLabelText('Shape');
  fireEvent.click(screen.getByText('Positive scalars'));
  expect(fetcher).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText('Rationale'), {target:{value:'Reviewed source'}});
  fireEvent.click(screen.getByRole('button', {name:'Approve 1 reviewed contracts'}));
  await waitFor(() => expect(screen.getByText(/Reviewed/)).toBeTruthy());
});

it('displays LaCASt context hints and applies them via Apply Context Hints button', async () => {
  const fetcher = vi.fn(async () => Response.json({
    items: [
      {
        symbol: 'Delta_g',
        inferred: { name: 'Delta_g', category: 'vector', domain: 'real', constraints: [], shape: ['?'], inference_confidence: 0.5 },
        latest_review: null,
        context_hint: {
          symbol: 'Delta_g',
          category: 'scalar',
          domain: 'positive',
          shape: [],
          constraints: ['Delta_g > 0'],
          evidence_span: 'learned quantization step Delta_g > 0',
        },
        w3c_intent: 'positive-scalar(Delta_g)',
      },
    ],
  }));
  vi.stubGlobal('fetch', fetcher);
  render(<BatchContractReviewPanel equation="eq-hint" />);
  fireEvent.click(screen.getByText('Batch Review'));
  expect(await screen.findByText(/learned quantization step/)).toBeTruthy();
  expect(screen.getByText(/intent: positive-scalar\(Delta_g\)/)).toBeTruthy();
  fireEvent.click(screen.getByText('Apply Context Hints'));
  expect(screen.getByDisplayValue('scalar')).toBe(screen.getByLabelText('category'));
  expect(screen.getByDisplayValue('positive')).toBe(screen.getByLabelText('domain'));
});
