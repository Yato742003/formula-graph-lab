// @vitest-environment jsdom
import './setup';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  it('labels examples as drafts and reports JSON readability rather than verified manifests', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ items: [], total: 0 })));
    render(<ProblemSpecPanel />);
    expect(screen.queryByText(/verified benchmark templates/i)).toBeNull();
    expect(screen.getByText('Draft example')).toBeTruthy();
    expect(screen.getByText('Draft · Transformer')).toBeTruthy();
    expect(screen.getByText('Draft · Theory')).toBeTruthy();
    expect(screen.getByText('13/13 JSON readable')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Use 3DGS question' }));
    expect(screen.getByText(/Draft 3DGS scaffold loaded/i)).toBeTruthy();
    expect(screen.getByText(/placeholder SHA-256 hashes/i)).toBeTruthy();
    expect(screen.queryByText(/Ready for G1 freeze/i)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Continue to manifest' }));
    fireEvent.change(screen.getByLabelText('Metrics and directions (JSON)'), { target: { value: '{' } });
    expect(screen.getByText('12/13 JSON readable')).toBeTruthy();
    expect(screen.queryByText(/Sections Configured/)).toBeNull();
  });
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
    expect(screen.queryByRole('checkbox', { name: /confirm these inputs should be frozen/i })).toBeNull();
  });

  it('does not expose wizard navigation for an immutable snapshot', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ items: [snapshot], total: 1 })));
    render(<ProblemSpecPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Measure causal attention latency · v1/ }));
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Review and freeze' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Create revision' })).toBeTruthy();
    expect(window.location.search).toContain('spec_id=spec_one');
    expect(screen.getByRole('link', { name: /Proceed to Step 3: Check Compatibility/i }).getAttribute('href'))
      .toBe('/compatibility?spec_id=spec_one');
    expect(screen.getByRole('link', { name: /Open Evolution in Reports/i }).getAttribute('href'))
      .toBe('/reports?spec_id=spec_one');
  });

  it('loads the registered CPU pilot draft and surfaces inline JSON syntax errors on Format', () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ items: [], total: 0 })));
    render(<ProblemSpecPanel />);
    fireEvent.click(screen.getByRole('button', { name: /Linear Attention CPU Pilot/i }));
    expect(screen.getByLabelText('Method family')).toHaveProperty('value', 'linear-attention');
    expect(screen.getByLabelText('Dtype')).toHaveProperty('value', 'float64');
    expect(screen.getByText(/attention-mashup-cpu-synthetic\.v1/i)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Continue to manifest' }));
    const metricsField = screen.getByLabelText('Metrics and directions (JSON)');
    fireEvent.change(metricsField, { target: { value: '{broken' } });
    expect(metricsField.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByText('Syntax error')).toBeTruthy();

    const formatButtons = screen.getAllByRole('button', { name: 'Format' });
    fireEvent.click(formatButtons[0]);
    expect(screen.getByText(/Metrics and directions: Invalid JSON/i)).toBeTruthy();
  });

  it('syncs Visual Manifest Builder edits into raw JSON fields and submits them on Freeze Spec', async () => {
    let submitted: Record<string, unknown> | undefined;
    vi.stubGlobal('fetch', vi.fn(async (_url, init?: RequestInit) => {
      if (init?.method !== 'POST') return Response.json({ items: [], total: 0 });
      submitted = JSON.parse(init.body as string) as Record<string, unknown>;
      return Response.json({
        spec_id: 'spec_visual',
        campaign_id: 'cmp_visual',
        version: 1,
        parent_spec_id: null,
        content_hash: 'f'.repeat(64),
        definition: submitted.definition,
      });
    }));

    render(<ProblemSpecPanel />);
    fireEvent.change(screen.getByLabelText('Research question'), { target: { value: 'Evaluate attention kernel perplexity' } });
    fireEvent.change(screen.getByLabelText('Method family'), { target: { value: 'attention' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue to manifest' }));

    const builder = screen.getByRole('region', { name: 'Visual manifest builder' });

    // Seed custom transform + extra metric to ensure visual edits preserve existing entries
    fireEvent.change(screen.getByLabelText('Metrics and directions (JSON)'), {
      target: {
        value: JSON.stringify([
          { name: 'rate_distortion_psnr', unit: 'dB', direction: 'maximize' },
          { name: 'wall_ms', unit: 'ms', direction: 'minimize' },
        ]),
      },
    });
    fireEvent.change(screen.getByLabelText('Allowed transform names and versions (JSON)'), {
      target: {
        value: JSON.stringify([
          { name: 'mix_positive_feature_maps', version: '1' },
          { name: 'lower_mixture_to_concatenation', version: '1' },
          { name: 'custom_kernel_fuse', version: '2' },
        ]),
      },
    });

    fireEvent.change(within(builder).getByLabelText('Primary metric name'), { target: { value: 'perplexity' } });
    fireEvent.change(within(builder).getByLabelText('Metric unit'), { target: { value: 'score' } });
    fireEvent.change(within(builder).getByLabelText('Optimization direction'), { target: { value: 'minimize' } });
    fireEvent.change(within(builder).getByLabelText('Guardrail relation'), { target: { value: '<=' } });
    fireEvent.change(within(builder).getByLabelText('Guardrail threshold'), { target: { value: '14.5' } });
    fireEvent.change(within(builder).getByLabelText('Hardware target'), { target: { value: 'cpu' } });
    fireEvent.change(within(builder).getByLabelText('Backend framework'), { target: { value: 'python-stdlib' } });
    fireEvent.change(within(builder).getByLabelText('Backend version'), { target: { value: '3.12' } });
    fireEvent.change(within(builder).getByLabelText('Max candidates'), { target: { value: '6' } });
    fireEvent.change(within(builder).getByLabelText('Run seeds (comma-separated)'), { target: { value: '7, 42, 1337' } });
    fireEvent.click(within(builder).getByRole('checkbox', { name: /lower_mixture_to_concatenation/i }));

    expect(JSON.parse(screen.getByLabelText<HTMLTextAreaElement>('Metrics and directions (JSON)').value)).toEqual([
      { name: 'perplexity', unit: 'score', direction: 'minimize' },
      { name: 'wall_ms', unit: 'ms', direction: 'minimize' },
    ]);
    expect(JSON.parse(screen.getByLabelText<HTMLTextAreaElement>('Quality constraints (JSON)').value)).toEqual([
      { metric: 'perplexity', relation: '<=', threshold: 14.5 },
    ]);
    expect(JSON.parse(screen.getByLabelText<HTMLTextAreaElement>('Pinned hardware (JSON)').value)).toEqual({
      target: 'cpu',
    });
    expect(JSON.parse(screen.getByLabelText<HTMLTextAreaElement>('Distinct run seeds (JSON)').value)).toEqual([7, 42, 1337]);
    expect(JSON.parse(screen.getByLabelText<HTMLTextAreaElement>('Allowed transform names and versions (JSON)').value)).toEqual([
      { name: 'mix_positive_feature_maps', version: '1' },
      { name: 'custom_kernel_fuse', version: '2' },
    ]);

    fireEvent.click(screen.getByRole('button', { name: 'Review and freeze' }));
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Freeze Spec' }));

    await waitFor(() => expect(submitted).toBeDefined());
    const submittedDef = submitted?.definition as Record<string, unknown>;
    expect(submittedDef.metrics).toEqual([
      { name: 'perplexity', unit: 'score', direction: 'minimize' },
      { name: 'wall_ms', unit: 'ms', direction: 'minimize' },
    ]);
    expect(submittedDef.quality_constraints).toEqual([
      { metric: 'perplexity', relation: '<=', threshold: 14.5 },
    ]);
    expect(submittedDef.hardware).toEqual({ target: 'cpu' });
    expect(submittedDef.backend).toEqual({ name: 'python-stdlib', version: '3.12' });
    expect(submittedDef.seeds).toEqual([7, 42, 1337]);
    expect((submittedDef.budget as Record<string, unknown>).max_candidates).toBe(6);
    expect((submittedDef.budget as Record<string, unknown>).compute_unit).toBe('GPU-seconds');
    expect(submittedDef.allowed_transforms).toEqual([
      { name: 'mix_positive_feature_maps', version: '1' },
      { name: 'custom_kernel_fuse', version: '2' },
    ]);
  });

  it('updates Visual Manifest Builder inputs live from raw JSON edits and degrades gracefully on invalid JSON', () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ items: [], total: 0 })));
    render(<ProblemSpecPanel />);
    fireEvent.change(screen.getByLabelText('Research question'), { target: { value: 'Check live two-way sync' } });
    fireEvent.change(screen.getByLabelText('Method family'), { target: { value: 'attention' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue to manifest' }));

    const builder = screen.getByRole('region', { name: 'Visual manifest builder' });
    const metricNameInput = within(builder).getByLabelText('Primary metric name');
    const metricDirectionSelect = within(builder).getByLabelText('Optimization direction');
    expect(metricNameInput).toHaveProperty('value', 'rate_distortion_psnr');

    const rawMetrics = screen.getByLabelText('Metrics and directions (JSON)');
    fireEvent.change(rawMetrics, {
      target: { value: JSON.stringify([{ name: 'decode_latency_ms', unit: 'ms', direction: 'minimize' }], null, 2) },
    });
    expect(metricNameInput).toHaveProperty('value', 'decode_latency_ms');
    expect(metricDirectionSelect).toHaveProperty('value', 'minimize');

    fireEvent.change(rawMetrics, { target: { value: '{malformed_json' } });
    expect(metricNameInput.matches(':disabled')).toBe(true);
    expect(metricDirectionSelect.matches(':disabled')).toBe(true);
    expect(within(builder).getByText(/Metrics JSON cannot be parsed right now/i)).toBeTruthy();
  });
});
