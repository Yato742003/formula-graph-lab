// @vitest-environment jsdom
import './setup';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ProblemSpecPanel from '../app/problem-spec-panel';

const definition = {
  task: 'Measure causal attention latency', method_family: 'attention', dtype: 'float32',
  metrics: [{ name: 'latency', unit: 'ms', direction: 'minimize' }],
  quality_constraints: [],
  baselines: [{ name: 'reference', version: '1', sha256: 'a'.repeat(64), readiness: 'unverified' }],
  dataset: { version: '1', artifact_hash: 'b'.repeat(64), splits: [{ role: 'search', split_hash: 'c'.repeat(64) }] },
  model: { status: 'not_applicable', reason: 'Kernel benchmark' },
  tokenizer: { status: 'not_applicable', reason: 'Synthetic tensor inputs' },
  hardware: { target: 'CPU-x86' }, backend: { name: 'numpy', version: '2.0.0' },
  evaluator: { name: 'reference', protocol_version: '1', implementation_hash: 'd'.repeat(64), config_hash: 'e'.repeat(64) },
  seeds: [42], budget: { max_candidates: 2, max_generations: 1, wall_time_ms: 1000, compute_budget: 1, compute_unit: 'CPU-seconds' },
  allowed_transforms: [], stop_conditions: [],
};
const snapshot = { spec_id: 'spec_one', campaign_id: 'cmp_one', version: 1,
  parent_spec_id: null, content_hash: 'f'.repeat(64), definition };
const labels: Record<string, string> = {
  metrics: 'Metrics and directions', quality_constraints: 'Quality constraints', baselines: 'Baseline artifacts',
  dataset: 'Dataset and splits', model: 'Model artifact or not_applicable with reason',
  tokenizer: 'Tokenizer artifact or not_applicable with reason', hardware: 'Pinned hardware',
  backend: 'Pinned backend', evaluator: 'Evaluator manifest', seeds: 'Distinct run seeds',
  budget: 'Search and compute limits', allowed_transforms: 'Allowed transform names and versions',
  stop_conditions: 'Metric stop conditions',
};

function fill() {
  fireEvent.change(screen.getByLabelText('Research question'), { target: { value: definition.task } });
  fireEvent.change(screen.getByLabelText('Method family'), { target: { value: definition.method_family } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue to manifest' }));
  for (const [key, label] of Object.entries(labels)) {
    fireEvent.change(screen.getByLabelText(`${label} (JSON)`), {
      target: { value: JSON.stringify(definition[key as keyof typeof definition]) },
    });
  }
  fireEvent.click(screen.getByRole('button', { name: 'Review and freeze' }));
  fireEvent.click(screen.getByRole('checkbox'));
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('ProblemSpec persistence workflow', () => {
  it('retries an unchanged failed save with the same key and preserves entered values', async () => {
    const requests: RequestInit[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_url, init?: RequestInit) => {
      if (init?.method !== 'POST') return Response.json({ items: [], total: 0 });
      requests.push(init);
      return requests.length === 1 ? new Response('{}', { status: 503 }) : Response.json(snapshot);
    }));
    render(<ProblemSpecPanel />); fill();
    fireEvent.click(screen.getByRole('button', { name: 'Freeze Spec' }));
    expect(await screen.findByText(/Could not save ProblemSpec/)).toBeTruthy();
    expect(screen.getByText(definition.task)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Freeze Spec' }));
    expect(await screen.findByText('ProblemSpec saved. Artifact verification is still pending.')).toBeTruthy();
    expect(new Headers(requests[0].headers).get('x-idempotency-key')).toBe(new Headers(requests[1].headers).get('x-idempotency-key'));
    expect(JSON.parse(requests[1].body as string).definition).toEqual(definition);
    expect(screen.getByText(definition.task)).toBeTruthy();
  });

  it('loads saved history and creates a separate revision without editing the original', async () => {
    let sent: Record<string, unknown> | undefined;
    vi.stubGlobal('fetch', vi.fn(async (_url, init?: RequestInit) => {
      if (init?.method !== 'POST') return Response.json({ items: [snapshot], total: 1 });
      sent = JSON.parse(init.body as string);
      return Response.json({ ...snapshot, spec_id: 'spec_two', campaign_id: 'cmp_two', version: 2,
        parent_spec_id: 'spec_one', definition: sent!.definition });
    }));
    render(<ProblemSpecPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Measure causal attention latency · v1/ }));
    expect(screen.getByLabelText('Metrics and directions (JSON)').matches(':disabled')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Create revision' }));
    fireEvent.change(screen.getByLabelText('Research question'), { target: { value: 'Revised question' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue to manifest' }));
    fireEvent.click(screen.getByRole('button', { name: 'Review and freeze' }));
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Freeze Spec' }));
    await waitFor(() => expect(sent?.parent_spec_id).toBe('spec_one'));
    expect(await screen.findByText('spec_two')).toBeTruthy();
    expect(snapshot.definition.task).toBe(definition.task);
    expect(screen.getByRole('button', { name: /Measure causal attention latency · v1/ })).toBeTruthy();
  });

  it('does not freeze on a success-shaped but incomplete receipt', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url, init?: RequestInit) => Response.json(
      init?.method === 'POST' ? { spec_id: 'spec_fake', campaign_id: 'cmp_fake', content_hash: 'f'.repeat(64) } : { items: [], total: 0 },
    )));
    render(<ProblemSpecPanel />); fill();
    fireEvent.click(screen.getByRole('button', { name: 'Freeze Spec' }));
    expect(await screen.findByText(/Invalid ProblemSpec response/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Spec Frozen' })).toBeNull();
  });

  it('requires scope and valid manifest JSON before showing the freeze gate', () => {
    render(<ProblemSpecPanel />);
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Continue to manifest' }));
    expect(screen.getByText(/Add a research question and method family/)).toBeTruthy();
    expect(screen.queryByLabelText('Metrics and directions (JSON)')).toBeNull();

    fireEvent.change(screen.getByLabelText('Research question'), { target: { value: 'Test a bounded hypothesis' } });
    fireEvent.change(screen.getByLabelText('Method family'), { target: { value: 'attention' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue to manifest' }));
    expect(screen.getByRole('button', { name: 'Back' })).toBeTruthy();
    const metrics = screen.getByLabelText('Metrics and directions (JSON)');
    expect(metrics.getAttribute('aria-describedby')).toBe('problem-spec-section-metrics-help');
    expect(screen.getByText(/Name each measured outcome/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Metrics and directions (JSON)'), { target: { value: '{' } });
    fireEvent.click(screen.getByRole('button', { name: 'Review and freeze' }));
    expect(screen.getByText(/Metrics and directions: enter valid JSON/)).toBeTruthy();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('does not expose wizard navigation for an immutable snapshot', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ items: [snapshot], total: 1 })));
    render(<ProblemSpecPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Measure causal attention latency · v1/ }));
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Review and freeze' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Create revision' })).toBeTruthy();
  });
});
