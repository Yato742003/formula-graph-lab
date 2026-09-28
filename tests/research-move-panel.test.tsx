// @vitest-environment jsdom

import './setup';

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import ResearchMovePanel from '../app/research-move-panel';
import type { CompatibilityView } from '../lib/research-view';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

const problemList = {
  total: 1,
  items: [
    {
      spec_id: 'spec_0123456789abcdef',
      version: 1,
      definition: {
        task: 'Improve causal attention latency',
        seeds: [29],
        allowed_transforms: [
          { name: 'mix_positive_feature_maps', version: '1' },
        ],
      },
    },
  ],
};

const mapping: CompatibilityView = {
  mappingId: 'map-reviewed-1',
  producer: {
    equationId: 'eq-a',
    equation: 'eq-a',
    symbolId: 'scope-a',
    symbol: 'phi_a',
    domain: 'strictly_positive_real',
    shape: '[64]',
  },
  consumer: {
    equationId: 'eq-b',
    equation: 'eq-b',
    symbolId: 'scope-b',
    symbol: 'phi_b',
    domain: 'strictly_positive_real',
    shape: '[64]',
  },
  status: 'compatible',
  freshness: 'current',
  policyVersion: 'compatibility-policy.v3',
  reasons: [],
  unresolved: [],
  isReviewed: true,
};

function candidate(
  id: string,
  activity: string,
  operator: string,
  semantics: string,
  parents: string[],
  obligations: { name: string; status: string }[],
) {
  return {
    candidate: {
      candidate_id: `cand_${id.padStart(32, '0')}`,
      operator,
      semantics_class: semantics,
      parents: parents.map((entity_id) => ({
        entity_id,
        version: 1,
        content_hash: 'a'.repeat(64),
      })),
      obligations,
    },
    activity: {
      activity_id: `act_${activity.padStart(32, '0')}`,
      created_at: '2026-09-25T04:00:00.000Z',
    },
    replayed: false,
  };
}

function admission(allowed = false, reasons = ['mapping_unknown']) {
  return {
    decision: {
      decision_id: 'pol_' + '1'.repeat(32),
      allowed,
      outcome: allowed ? 'allowed' : 'denied',
      rule_id: allowed ? 'FGL-V0-NUMERICAL-READY' : 'FGL-V0-DENY-DEFAULT',
      reasons,
      input_result_ids: ['chk_' + 'c'.repeat(32)],
    },
    replayed: false,
  };
}

function candidateCheck(candidateId: string, outcome = 'unknown') {
  return {
    check: {
      check_id: 'chk_' + 'c'.repeat(32),
      candidate_id: candidateId,
      checker_version: 'candidate-static.v4',
      claim:
        'The compiled weighted-kernel mixture has the declared typed structure.',
      scope: 'candidate_structure',
      assumptions: [],
      input_hashes: ['d'.repeat(64)],
      outcome,
      vector: { parse: 'supported', type: 'unknown', domain: 'unresolved' },
    },
    replayed: false,
  };
}

function numericalFixture(
  candidateId: string,
  specId: string,
  outcome = 'passed_suite',
) {
  return {
    result_id: 'num_' + 'e'.repeat(32),
    result_hash: 'f'.repeat(64),
    run_id: '00000000-0000-4000-8000-000000000029',
    workspace_id: 'workspace-1',
    actor_id: 'user-1',
    candidate_id: candidateId,
    candidate_hash: 'a'.repeat(64),
    parent_refs: [],
    problem_spec_id: specId,
    problem_spec_hash: 'b'.repeat(64),
    suite_version: 'feature-kernel-fixture.v1',
    input_hash: 'c'.repeat(64),
    execution_image: 'sha256:' + 'd'.repeat(64),
    seed: 29,
    dtype: 'float32',
    tolerance: 0.00001,
    outcome,
    checks: { registered_identity: outcome === 'passed_suite' },
    measurements: {},
    counterexample: null,
    environment: {},
    error_code: null,
    fixture_scope: 'synthetic_feature_kernel_fixture',
    performance_claim: false,
    created_at: '2026-09-25T04:02:00.000Z',
    schema_version: 'numerical-fixture-result.v1',
  };
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.pathname;
  return input.url;
}

function requestBody(init?: RequestInit): Record<string, unknown> {
  return typeof init?.body === 'string'
    ? JSON.parse(init.body) as Record<string, unknown>
    : {};
}

function sourceSpanId(entityId: string, anchor: string) {
  const bytes = new TextEncoder().encode(JSON.stringify([entityId, anchor]));
  const binary = Array.from(bytes, (byte) => String.fromCharCode(byte)).join('');
  return `span_${btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')}`;
}

function savedProposal(review: unknown = null) {
  return {
    created_at: '2026-09-25T00:00:00+00:00',
    review,
    proposal: {
      proposal_id: 'prop_' + 'a'.repeat(32),
      content_hash: 'b'.repeat(64),
      problem_spec_id: 'spec_0123456789abcdef',
      parent_ids: ['eq-a', 'eq-b'],
      source_span_ids: [sourceSpanId('source-1', 'S1.E1')],
      operator: 'mix_positive_feature_maps',
      operator_version: '1',
      semantics_class: 'hypothesis_changing',
      transform_json: JSON.stringify({
        operator: 'mix_positive_feature_maps',
        operator_version: '1',
        target_node_id: 'eq-a',
        parameters: { lambda: 0.25 },
        bindings: { left: 'scope-a', right: 'scope-b' },
      }),
      assumptions: [{ text: 'Features remain positive.', origin: 'ai_proposed', discharged: false }],
      rationale: 'Explore a source-backed mixture.',
      expected_effect: 'Potential latency reduction.',
      review_state: 'pending',
    },
  };
}

function mockApi(
  fetcher: ReturnType<typeof vi.fn>,
  candidateHistory: { items: unknown[]; total: number } = {
    items: [],
    total: 0,
  },
  proposalHistory: { items: unknown[]; total: number } | null = {
    items: [],
    total: 0,
  },
  proposalCapability: unknown = {
    state: 'configured',
    model: 'test-model',
    max_generation_cost_usd: '0.02',
    daily_max_cost_usd: '0.10',
  },
) {
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
    requestPath(input) === '/api/research/proposals/capabilities'
      ? Promise.resolve(Response.json(proposalCapability))
      : requestPath(input).startsWith('/api/research/candidates?')
      ? Promise.resolve(Response.json(candidateHistory))
      : requestPath(input).startsWith('/api/research/proposals?')
        ? Promise.resolve(
            proposalHistory
              ? Response.json(proposalHistory)
              : new Response('{}', { status: 503 }),
          )
      : (
          fetcher as unknown as (
            input: RequestInfo | URL,
            init?: RequestInit,
          ) => Promise<Response>
        )(input, init),
  );
}

function compilerReplayReport(candidateId: string, activityId: string) {
  return {
    schema_version: 'compiler-replay-report.v1',
    report_hash: 'e'.repeat(64),
    status: 'partial',
    scope: 'compiler_replay_only',
    workspace_id: 'workspace-1',
    candidate_id: candidateId,
    candidate_hash: 'f'.repeat(64),
    activity_id: activityId,
    bundle_hash: 'a'.repeat(64),
    operator: 'mix_positive_feature_maps',
    operator_version: '1',
    semantics_class: 'hypothesis_changing',
    source_refs: [{
      equation_id: 'eq-a',
      equation_source_hash: 'b'.repeat(64),
      paper_id: '1706.03762',
      paper_version: 2,
      paper_version_id: 'paper-v2',
      paper_html_hash: 'c'.repeat(64),
      source_url: 'https://arxiv.org/html/1706.03762v2',
      anchor: 'S3.E1',
      anchor_is_source: true,
      resolver: 'arxiv-html-anchor-sha256.v1',
    }],
    lineage_assertion_ids: [],
    compatibility_mapping_id: 'map-reviewed-1',
    proposal_review_status: 'not_applicable',
    compiler_replay: 'reproduced',
    checks: [{
      check_id: 'chk_' + '2'.repeat(32),
      checker_version: 'candidate-static.v4',
      claim: 'Only this scoped structure was checked.',
      scope: 'candidate_structure',
      replay_status: 'replayed',
      outcome: 'unknown',
      type_status: 'unknown',
      domain_status: 'unresolved',
      numerical_status: 'not_run',
      empirical_status: 'not_run',
    }],
    policy_decisions: [{
      decision_id: 'pol_' + '3'.repeat(32),
      action: 'can_run_experiment',
      outcome: 'denied',
      reasons: ['budget_or_quota_unavailable'],
      replay_status: 'replayed',
    }],
    numerical_fixtures: [{
      result_id: 'num_' + '4'.repeat(32),
      outcome: 'passed_suite',
      seed: 29,
      scope: 'synthetic_feature_kernel_fixture',
      replay_status: 'replayed',
      performance_claim: false,
    }],
    empirical_experiment: 'not_run',
    limitations: [
      'Compiler replay and stored checks only.',
      'Synthetic fixture is diagnostic only.',
      'No frozen matched-control holdout run is attached.',
    ],
  };
}

it('compiles a hypothesis from a frozen spec and reviewed mapping without calling it verified', async () => {
  const requests: string[] = [];
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      if (requestPath(input).startsWith('/api/research/problems?'))
        return Response.json(problemList);
      if (requestPath(input).endsWith('/verify')) {
        const candidateId = requestPath(input).split('/').at(-2) ?? '';
        return Response.json(candidateCheck(candidateId));
      }
      if (requestPath(input).endsWith('/admission'))
        return Response.json(admission());
      requests.push(typeof init?.body === 'string' ? init.body : '');
      return Response.json(
        candidate(
          '1',
          '1',
          'mix_positive_feature_maps',
          'hypothesis_changing',
          ['eq-a', 'eq-a:scope-a', 'eq-b:scope-b'],
          [
            { name: 'compatible_ports', status: 'discharged' },
            { name: 'nonzero_normalization_denominator', status: 'unresolved' },
          ],
        ),
        { status: 201 },
      );
    },
  );
  mockApi(fetcher);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );
  await user.click(screen.getByText('Create research candidate'));
  const weightInput = await screen.findByLabelText('Mixture weight lambda');
  fireEvent.change(weightInput, { target: { value: '' } });
  expect(
    screen.queryByRole('region', { name: 'Transformation preview' }),
  ).toBeNull();
  expect(
    screen.getByRole('button', { name: 'Compile hypothesis' }),
  ).toHaveProperty('disabled', true);
  fireEvent.change(weightInput, {
    target: { value: '0.25' },
  });
  expect(
    screen.getByRole('region', { name: 'Transformation preview' }).textContent,
  ).toMatch(/κmix\(q,k\) = 0\.25[\s\S]*0\.75/);
  expect(screen.getByText(/does not change source formulas/i)).toBeTruthy();
  expect(
    screen.getByText(/lightweight deterministic CPU checks only/i),
  ).toBeTruthy();
  await user.click(screen.getByRole('button', { name: 'Compile hypothesis' }));

  expect(
    await screen.findByText(
      /No numerical suite or empirical experiment has run/i,
    ),
  ).toBeTruthy();
  expect(screen.getByText('Unknown')).toBeTruthy();
  expect(screen.getByText('unresolved')).toBeTruthy();
  expect(screen.queryByText(/^passed$/i)).toBeNull();
  expect(
    await screen.findByText(/Stored policy decision: denied/i),
  ).toBeTruthy();
  expect(screen.getByText(/Evidence receipts:/i).textContent).toMatch(
    /chk_c{32}/i,
  );
  expect(
    await screen.findByText(
      /Independent check: unknown · candidate_structure · checker candidate-static\.v4 · domain unresolved/i,
    ),
  ).toBeTruthy();
  expect(screen.getByText(/checks the declared structure only/i)).toBeTruthy();
  expect(
    screen.getByText(
      /Verification vector: parse supported · type unknown · domain unresolved · symbolic unknown/i,
    ),
  ).toBeTruthy();
  expect(screen.getByText(/pol_1{32}/i)).toBeTruthy();
  expect(
    screen.getByText(/no mapping handle for a freshness re-check/i),
  ).toBeTruthy();
  const admissionCall = fetcher.mock.calls.find(([input]) =>
    requestPath(input).endsWith('/admission'),
  );
  expect(JSON.parse(admissionCall?.[1]?.body as string)).toEqual({
    action: 'can_run_numerical',
  });
  expect(
    new Headers(admissionCall?.[1]?.headers).get('x-idempotency-key'),
  ).toBeTruthy();
  const verificationCall = fetcher.mock.calls.find(([input]) =>
    requestPath(input).endsWith('/verify'),
  );
  expect(JSON.parse(verificationCall?.[1]?.body as string)).toEqual({});
  expect(
    new Headers(verificationCall?.[1]?.headers).get('x-idempotency-key'),
  ).toBeTruthy();
  expect(JSON.parse(requests[0])).toEqual({
    spec_id: 'spec_0123456789abcdef',
    mapping_id: 'map-reviewed-1',
    transform: {
      operator: 'mix_positive_feature_maps',
      operator_version: '1',
      target_node_id: 'eq-a',
      parameters: { lambda: 0.25 },
      bindings: { left: 'scope-a', right: 'scope-b' },
    },
  });
  await user.click(screen.getByRole('button', { name: 'Close' }));
  expect(
    screen.getByRole('button', { name: /Policy denied · no run/i }),
  ).toBeTruthy();
});

it('opens the composer as a dialog and closes it on Escape', async () => {
  const fetcher = vi.fn(async () => Response.json(problemList));
  mockApi(fetcher);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );

  const trigger = screen.getByRole('button', {
    name: /Create research candidate/i,
  });
  await user.click(trigger);

  expect(screen.getByRole('dialog')).toBeTruthy();
  expect(
    screen.getByRole('heading', { name: 'Create research candidate' }),
  ).toBeTruthy();
  expect(screen.getByText(/compiler records a hypothesis/i)).toBeTruthy();
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});

it('shows persisted model proposals as untrusted drafts with resolvable source references', async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = requestPath(input);
    if (path.startsWith('/api/research/problems?')) return Response.json(problemList);
    if (path.startsWith('/api/research/lineage/sources?')) return Response.json({
      items: [{
        id: 'source-1', kind: 'equation', paper_id: '1706.03762', version: 7,
        anchor: 'S1.E1', text: 'Stored source excerpt.', latex: 'x',
        source_hash: 'a'.repeat(64), source_span_id: sourceSpanId('source-1', 'S1.E1'),
      }],
    });
    return Response.json({ items: [], total: 0 });
  });
  mockApi(fetcher, { items: [], total: 0 }, {
    total: 1,
    items: [{
      created_at: '2026-09-25T00:00:00+00:00',
      proposal: {
        proposal_id: 'prop_' + 'a'.repeat(32),
        content_hash: 'b'.repeat(64),
        problem_spec_id: 'spec_0123456789abcdef',
        parent_ids: ['equation-1'],
        source_span_ids: [sourceSpanId('source-1', 'S1.E1')],
        operator: 'mix_positive_feature_maps',
        operator_version: '1',
        semantics_class: 'hypothesis_changing',
        transform_json: '{"operator":"mix_positive_feature_maps"}',
        assumptions: [{ text: 'Features remain positive.', origin: 'ai_proposed', discharged: false }],
        rationale: 'Explore a source-backed mixture.',
        expected_effect: 'Potential latency reduction.',
        review_state: 'pending',
      },
    }],
  });
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );
  await user.click(screen.getByRole('button', { name: /Create research candidate/i }));
  expect(await screen.findByText(/AI-proposed · pending human review/i)).toBeTruthy();
  expect(screen.getByText(/untrusted proposal, not a candidate/i)).toBeTruthy();
  expect(screen.getByText(/no calibrated confidence score is available/i)).toBeTruthy();
  await user.click(screen.getByText(/Proposal rationale, assumptions, and source IDs/i));
  expect(await screen.findByText('Features remain positive.')).toBeTruthy();
  expect(screen.getByText(sourceSpanId('source-1', 'S1.E1'))).toBeTruthy();
  await user.click(screen.getByRole('button', { name: 'Resolve source' }));
  expect(await screen.findByText('Stored source excerpt.')).toBeTruthy();
  const sourceLink = screen.getByRole('link', { name: 'Open paper source ↗' });
  expect(sourceLink.getAttribute('href')).toBe('https://arxiv.org/html/1706.03762v7#S1.E1');
  expect(sourceLink.getAttribute('rel')).toBe('noopener noreferrer');
});

it('checks provider readiness before loading equations or requesting consent', async () => {
  const sourceRequests: string[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = requestPath(input);
    if (path.startsWith('/api/research/problems?')) return Response.json(problemList);
    if (path.startsWith('/api/research/lineage/sources?')) sourceRequests.push(path);
    return Response.json({ items: [], total: 0 });
  });
  mockApi(fetcher, { items: [], total: 0 }, { items: [], total: 0 }, {
    state: 'provider_not_configured',
  });
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );
  await user.click(screen.getByRole('button', { name: /Create research candidate/i }));
  await user.click(screen.getByRole('button', { name: 'Draft a research hypothesis with AI' }));
  expect(await screen.findByText(/missing provider configuration/i)).toBeTruthy();
  expect(screen.getByText(/No equation sources were loaded or sent/i)).toBeTruthy();
  expect(screen.queryByLabelText('Research question')).toBeNull();
  expect(screen.queryByRole('checkbox', { name: /I agree to send this question/i })).toBeNull();
  expect(sourceRequests).toEqual([]);
});

it('requires explicit disclosure consent before sending selected equations to AI', async () => {
  const sourceA = sourceSpanId('eq-a', 'S1.E1');
  const sourceB = sourceSpanId('eq-b', 'S2.E1');
  let generationBody: Record<string, unknown> | null = null;
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input);
    if (path.startsWith('/api/research/problems?')) return Response.json(problemList);
    if (path.startsWith('/api/research/lineage/sources?kind=equation')) return Response.json({
      items: [
        { id: 'eq-a', kind: 'equation', paper_id: '1706.03762', version: 7, anchor: 'S1.E1', latex: '\\phi_a(x)', source_span_id: sourceA, symbol_refs: [{ id: 'sym-a', notation: '\\phi_a' }] },
        { id: 'eq-b', kind: 'equation', paper_id: '1706.03763', version: 2, anchor: 'S2.E1', latex: '\\phi_b(x)', source_span_id: sourceB, symbol_refs: [{ id: 'sym-b', notation: '\\phi_b' }] },
      ],
    });
    if (path === '/api/research/proposals/generate' && init?.method === 'POST') {
      generationBody = requestBody(init);
      return Response.json({ proposal: { proposal_id: 'prop_' + 'a'.repeat(32), review_state: 'pending' } }, { status: 201 });
    }
    return Response.json({ items: [], total: 0 });
  });
  mockApi(fetcher);
  const user = userEvent.setup();
  render(<ResearchMovePanel mappings={[mapping]} mappingsState="loaded" mappingsPartial={false} />);
  await user.click(screen.getByRole('button', { name: /Create research candidate/i }));
  await user.click(screen.getByRole('button', { name: 'Draft a research hypothesis with AI' }));
  expect(screen.getByText(/no calibrated confidence score/i)).toBeTruthy();
  const costDisclosure = await screen.findByText(/per-generation ceiling/i);
  expect(costDisclosure.textContent).toMatch(/test-model.*\$0\.02.*up to 3 provider calls.*daily workspace ceiling \$0\.10/);
  await screen.findByText('1706.03762 v7 · S1.E1');
  const generate = screen.getByRole('button', { name: 'Generate one proposal' });
  expect(generate.hasAttribute('disabled')).toBe(true);
  await user.click(screen.getByLabelText(/1706\.03762 v7/i));
  await user.click(screen.getByLabelText(/1706\.03763 v2/i));
  await user.type(screen.getByLabelText('Research question'), 'Test a convex mixture');
  const consent = screen.getByRole('checkbox', { name: /I agree to send this question/i });
  expect(consent.hasAttribute('disabled')).toBe(false);
  await user.click(consent);
  expect(generate.hasAttribute('disabled')).toBe(false);
  expect(generationBody).toBeNull();
  await user.click(generate);
  await screen.findByText(/Saved as an untrusted proposal/i);
  expect(generationBody).toEqual({
    spec_id: 'spec_0123456789abcdef',
    parent_ids: ['eq-a', 'eq-b'],
    source_span_ids: [sourceA, sourceB],
    research_question: 'Test a convex mixture',
  });
});

it('reuses the pending AI request key after an uncertain response and reload', async () => {
  const sourceA = sourceSpanId('eq-a', 'S1.E1');
  const sourceB = sourceSpanId('eq-b', 'S2.E1');
  const requestKeys: string[] = [];
  let generationCalls = 0;
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input);
    if (path.startsWith('/api/research/problems?')) return Response.json(problemList);
    if (path.startsWith('/api/research/lineage/sources?kind=equation')) return Response.json({
      items: [
        { id: 'eq-a', kind: 'equation', paper_id: '1706.03762', version: 7, anchor: 'S1.E1', latex: '\\phi_a(x)', source_span_id: sourceA, symbol_refs: [{ id: 'sym-a', notation: '\\phi_a' }] },
        { id: 'eq-b', kind: 'equation', paper_id: '1706.03763', version: 2, anchor: 'S2.E1', latex: '\\phi_b(x)', source_span_id: sourceB, symbol_refs: [{ id: 'sym-b', notation: '\\phi_b' }] },
      ],
    });
    if (path === '/api/research/proposals/generate' && init?.method === 'POST') {
      requestKeys.push(new Headers(init.headers).get('x-idempotency-key') ?? '');
      generationCalls += 1;
      return generationCalls === 1
        ? Response.json({ detail: { code: 'PROPOSAL_GENERATION_UNAVAILABLE', reason: 'PROPOSAL_PROVIDER_UNAVAILABLE' } }, { status: 503 })
        : Response.json({ proposal: { proposal_id: 'prop_' + 'a'.repeat(32), review_state: 'pending' } }, { status: 200 });
    }
    return Response.json({ items: [], total: 0 });
  });
  mockApi(fetcher);
  const user = userEvent.setup();
  const props = { mappings: [mapping], mappingsState: 'loaded' as const, mappingsPartial: false };
  const fillAndSend = async (researchQuestion = 'Test a convex mixture') => {
    await user.click(screen.getByRole('button', { name: /Create research candidate/i }));
    await user.click(screen.getByRole('button', { name: 'Draft a research hypothesis with AI' }));
    await screen.findByText('1706.03762 v7 · S1.E1');
    await user.click(screen.getByLabelText(/1706\.03762 v7/i));
    await user.click(screen.getByLabelText(/1706\.03763 v2/i));
    await user.type(screen.getByLabelText('Research question'), researchQuestion);
    await user.click(screen.getByRole('checkbox', { name: /I agree to send this question/i }));
    await user.click(screen.getByRole('button', { name: 'Generate one proposal' }));
  };
  const firstMount = render(<ResearchMovePanel {...props} />);
  await fillAndSend();
  expect(await screen.findByText(/Check proposal history before requesting another generation/i)).toBeTruthy();
  firstMount.unmount();

  render(<ResearchMovePanel {...props} />);
  await fillAndSend('A different question');
  expect(await screen.findByText(/A previous request may still be active/i)).toBeTruthy();
  expect(requestKeys).toHaveLength(1);
  const question = screen.getByLabelText('Research question');
  await user.clear(question);
  await user.type(question, 'Test a convex mixture');
  await user.click(screen.getByRole('button', { name: 'Generate one proposal' }));
  expect(await screen.findByText(/Saved as an untrusted proposal/i)).toBeTruthy();
  expect(requestKeys).toHaveLength(2);
  expect(requestKeys[0]).toBeTruthy();
  expect(requestKeys[1]).toBe(requestKeys[0]);
});

it('requires explicit human review before compiling the exact saved proposal', async () => {
  const sourceId = sourceSpanId('source-1', 'S1.E1');
  const history = { total: 1, items: [savedProposal()] };
  let compileBody: Record<string, unknown> | null = null;
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input);
    if (path.startsWith('/api/research/problems?')) return Response.json(problemList);
    if (path.startsWith('/api/research/lineage/sources?')) return Response.json({
      items: [{
        id: 'source-1', kind: 'equation', paper_id: '1706.03762', version: 7,
        anchor: 'S1.E1', text: 'Stored source excerpt.', latex: 'x',
        source_hash: 'a'.repeat(64), source_span_id: sourceId,
      }],
    });
    if (path.endsWith('/reviews') && init?.method === 'POST') {
      const request = requestBody(init) as { decision: string; notes: string };
      const review = {
        review_id: 'prev_' + 'd'.repeat(32),
        review_hash: 'e'.repeat(64),
        workspace_id: 'workspace-1',
        proposal_id: 'prop_' + 'a'.repeat(32),
        proposal_hash: 'b'.repeat(64),
        reviewer_id: 'user-1',
        reviewer_role: 'researcher',
        decision: request.decision,
        notes: request.notes,
        reviewed_at: '2026-09-25T00:00:00+00:00',
        schema_version: 'proposal-review.v1',
      };
      history.items[0].review = review;
      return Response.json({ review, replayed: false }, { status: 201 });
    }
    if (path === '/api/research/candidates/compile') {
      compileBody = requestBody(init);
      return Response.json({ code: 'SIMULATED_COMPILER_UNAVAILABLE' }, { status: 502 });
    }
    return Response.json({ items: [], total: 0 });
  });
  mockApi(fetcher, { items: [], total: 0 }, history);
  const user = userEvent.setup();
  render(<ResearchMovePanel mappings={[mapping]} mappingsState="loaded" mappingsPartial={false} />);
  await user.click(screen.getByRole('button', { name: /Create research candidate/i }));
  await user.type(
    await screen.findByLabelText('Human review rationale'),
    'I reviewed the cited source and bindings.',
  );
  await user.click(screen.getByRole('button', { name: 'Accept for compilation only' }));
  expect(await screen.findByText(/no mathematical claim was verified/i)).toBeTruthy();
  await user.click(screen.getByRole('button', { name: 'Compile this stored proposal' }));
  await waitFor(() => expect(compileBody).not.toBeNull());
  expect(compileBody).toMatchObject({
    spec_id: 'spec_0123456789abcdef',
    mapping_id: 'map-reviewed-1',
    proposal_id: 'prop_' + 'a'.repeat(32),
    transform: JSON.parse(history.items[0].proposal.transform_json),
  });
  expect(await screen.findByText(/Compiler request rejected \(502\)/i)).toBeTruthy();
});

it('keeps a rejected proposal terminal and never offers its compile action', async () => {
  const history = { total: 1, items: [savedProposal()] };
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input);
    if (path.startsWith('/api/research/problems?')) return Response.json(problemList);
    if (path.endsWith('/reviews') && init?.method === 'POST') {
      const request = requestBody(init) as { notes: string };
      const review = {
        review_id: 'prev_' + 'd'.repeat(32), review_hash: 'e'.repeat(64),
        workspace_id: 'workspace-1', proposal_id: 'prop_' + 'a'.repeat(32),
        proposal_hash: 'b'.repeat(64), reviewer_id: 'user-1', reviewer_role: 'researcher',
        decision: 'reject', notes: request.notes, reviewed_at: '2026-09-25T00:00:00+00:00',
        schema_version: 'proposal-review.v1',
      };
      history.items[0].review = review;
      return Response.json({ review, replayed: false }, { status: 201 });
    }
    return Response.json({ items: [], total: 0 });
  });
  mockApi(fetcher, { items: [], total: 0 }, history);
  const user = userEvent.setup();
  render(<ResearchMovePanel mappings={[mapping]} mappingsState="loaded" mappingsPartial={false} />);
  await user.click(screen.getByRole('button', { name: /Create research candidate/i }));
  await user.type(await screen.findByLabelText('Human review rationale'), 'Reject this branch.');
  await user.click(screen.getByRole('button', { name: 'Reject proposal' }));
  expect(await screen.findByText(/decision is final for this proposal/i)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Compile this stored proposal' })).toBeNull();
});

it('does not imply source verification when proposal context cannot be resolved', async () => {
  const sourceId = sourceSpanId('source-1', 'S1.E1');
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = requestPath(input);
    if (path.startsWith('/api/research/problems?')) return Response.json(problemList);
    if (path.startsWith('/api/research/lineage/sources?')) return new Response('{}', { status: 503 });
    return Response.json({ items: [], total: 0 });
  });
  mockApi(fetcher, { items: [], total: 0 }, {
    total: 1,
    items: [{ created_at: '2026-09-25T00:00:00Z', proposal: {
      proposal_id: 'prop_' + 'a'.repeat(32), content_hash: 'b'.repeat(64),
      problem_spec_id: 'spec_0123456789abcdef', parent_ids: ['equation-1'],
      source_span_ids: [sourceId], operator: 'mix_positive_feature_maps',
      operator_version: '1', semantics_class: 'hypothesis_changing',
      transform_json: '{"operator":"mix_positive_feature_maps"}', assumptions: [],
      rationale: 'Untrusted proposal rationale.', expected_effect: 'Unknown.', review_state: 'pending',
    } }],
  });
  const user = userEvent.setup();
  render(<ResearchMovePanel mappings={[mapping]} mappingsState="loaded" mappingsPartial={false} />);
  await user.click(screen.getByRole('button', { name: /Create research candidate/i }));
  await user.click(await screen.findByText(/Proposal rationale, assumptions, and source IDs/i));
  await user.click(screen.getByRole('button', { name: 'Resolve source' }));
  expect(await screen.findByText(/Source unavailable; proposal remains unverified/i)).toBeTruthy();
  expect(screen.queryByRole('link', { name: 'Open paper source ↗' })).toBeNull();
});

it('does not show an empty proposal count when history is unavailable', async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) =>
    requestPath(input).startsWith('/api/research/problems?')
      ? Response.json(problemList)
      : Response.json({ items: [], total: 0 }),
  );
  mockApi(fetcher, { items: [], total: 0 }, null);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );
  await user.click(screen.getByRole('button', { name: /Create research candidate/i }));
  const section = document.querySelector('section[aria-label="Saved AI proposals"]');
  expect(section?.textContent).toContain('Proposal history unavailable');
  expect(section?.textContent).not.toContain('0 recorded');
  expect(await screen.findByText(/Existing candidates and checks are unchanged/i)).toBeTruthy();
});

it('shows the synthetic worker result as a diagnostic, not proof or policy admission', async () => {
  const requests: { path: string; body: string; key: string | null }[] = [];
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path.startsWith('/api/research/problems?'))
        return Response.json(problemList);
      if (path.endsWith('/verify')) {
        const candidateId = path.split('/').at(-2) ?? '';
        return Response.json(candidateCheck(candidateId));
      }
      if (path.endsWith('/admission')) return Response.json(admission());
      if (path.endsWith('/numerical-fixture'))
        return Response.json(
          numericalFixture(
            'cand_' + '0'.repeat(31) + '1',
            'spec_0123456789abcdef',
          ),
        );
      requests.push({
        path,
        body: typeof init?.body === 'string' ? init.body : '',
        key: new Headers(init?.headers).get('x-idempotency-key'),
      });
      return Response.json(
        candidate(
          '1',
          '1',
          'mix_positive_feature_maps',
          'hypothesis_changing',
          ['eq-a'],
          [{ name: 'nonzero_normalization_denominator', status: 'unresolved' }],
        ),
        { status: 201 },
      );
    },
  );
  mockApi(fetcher);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );

  await user.click(screen.getByText('Create research candidate'));
  await user.click(
    await screen.findByRole('button', { name: 'Compile hypothesis' }),
  );
  await user.click(
    screen.getByText('Optional worker fixture · diagnostic only'),
  );
  const run = screen.getByRole('button', {
    name: 'Run synthetic diagnostic · seed 29',
  });
  await user.click(run);

  expect(await screen.findByText('Recorded outcome:')).toBeTruthy();
  expect(screen.getByText('passed_suite')).toBeTruthy();
  expect(screen.getByText(/does not run a paper implementation/i)).toBeTruthy();
  expect(screen.getAllByText(/Admission is unchanged/i).length).toBeGreaterThan(
    0,
  );
  expect(screen.getByText(/Policy denied · no run/i)).toBeTruthy();
  expect(
    screen.getByText(
      /No paper implementation or empirical experiment has been tested/i,
    ),
  ).toBeTruthy();
  const fixtureCall = fetcher.mock.calls.find(([input]) =>
    requestPath(input).endsWith('/numerical-fixture'),
  );
  expect(JSON.parse(fixtureCall?.[1]?.body as string)).toEqual({ seed: 29 });
  expect(
    new Headers(fixtureCall?.[1]?.headers).get('x-idempotency-key'),
  ).toBeTruthy();
  expect(requests).toHaveLength(1);
  expect(JSON.parse(requests[0].body).transform.operator).toBe(
    'mix_positive_feature_maps',
  );
});

it('keeps fixture failure distinct and reuses the idempotency key on retry', async () => {
  const fixtureKeys: string[] = [];
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path.startsWith('/api/research/problems?'))
        return Response.json(problemList);
      if (path.endsWith('/verify')) {
        const candidateId = path.split('/').at(-2) ?? '';
        return Response.json(candidateCheck(candidateId));
      }
      if (path.endsWith('/admission')) return Response.json(admission());
      if (path.endsWith('/numerical-fixture')) {
        fixtureKeys.push(
          new Headers(init?.headers).get('x-idempotency-key') ?? '',
        );
        return Response.json({ code: 'UPSTREAM_UNAVAILABLE' }, { status: 502 });
      }
      return Response.json(
        candidate(
          '1',
          '1',
          'mix_positive_feature_maps',
          'hypothesis_changing',
          ['eq-a'],
          [],
        ),
        { status: 201 },
      );
    },
  );
  mockApi(fetcher);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );
  await user.click(screen.getByText('Create research candidate'));
  await user.click(
    await screen.findByRole('button', { name: 'Compile hypothesis' }),
  );
  await user.click(
    screen.getByText('Optional worker fixture · diagnostic only'),
  );
  const run = screen.getByRole('button', {
    name: 'Run synthetic diagnostic · seed 29',
  });
  await user.click(run);
  expect(await screen.findByText(/no result was confirmed/i)).toBeTruthy();
  await user.click(run);
  await waitFor(() => expect(fixtureKeys).toHaveLength(2));
  expect(fixtureKeys[0]).toBe(`fixture:cand_${'0'.repeat(31)}1:29`);
  expect(fixtureKeys[1]).toBe(fixtureKeys[0]);
  expect(screen.queryByText(/Recorded outcome:/i)).toBeNull();
  expect(screen.getByText(/Policy denied · no run/i)).toBeTruthy();
});

it('records concatenation lowering as a separate activity linked to the parent', async () => {
  const requests: string[] = [];
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      if (requestPath(input).startsWith('/api/research/problems?'))
        return Response.json(problemList);
      if (requestPath(input).endsWith('/verify')) {
        const candidateId = requestPath(input).split('/').at(-2) ?? '';
        return Response.json(candidateCheck(candidateId, 'supported'));
      }
      if (requestPath(input).endsWith('/admission'))
        return Response.json(admission());
      requests.push(typeof init?.body === 'string' ? init.body : '');
      return requests.length === 1
        ? Response.json(
            candidate(
              '1',
              '1',
              'mix_positive_feature_maps',
              'hypothesis_changing',
              ['eq-a', 'eq-a:scope-a', 'eq-b:scope-b'],
              [],
            ),
            { status: 201 },
          )
        : Response.json(
            candidate(
              '2',
              '2',
              'lower_mixture_to_concatenation',
              'preserving',
              ['cand_' + '0'.repeat(31) + '1'],
              [{ name: 'kernel_identity', status: 'unresolved' }],
            ),
            { status: 201 },
          );
    },
  );
  mockApi(fetcher);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );
  await user.click(screen.getByText('Create research candidate'));
  await user.click(screen.getByRole('button', { name: 'Compile hypothesis' }));
  await user.click(
    await screen.findByRole('button', {
      name: 'Add separate concatenation representation',
    }),
  );

  await waitFor(() => expect(requests).toHaveLength(2));
  const second = JSON.parse(requests[1]);
  expect(second.parent_candidate_id).toBe('cand_' + '0'.repeat(31) + '1');
  expect(second.transform.operator).toBe('lower_mixture_to_concatenation');
  expect(second.transform.bindings.mixture).toBe(second.parent_candidate_id);
  expect(
    screen.getByText(/No numerical suite or empirical experiment has run/i),
  ).toBeTruthy();
});

it('explains why unknown, stale, or unreviewed mappings cannot be compiled', async () => {
  const notUsable = {
    ...mapping,
    status: 'unknown' as const,
    freshness: 'stale' as const,
    isReviewed: false,
  };
  const fetcher = vi.fn(async () => Response.json(problemList));
  mockApi(fetcher);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[notUsable]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );
  await user.click(screen.getByText('Create research candidate'));
  expect(
    await screen.findByText(/Create and review a current compatible mapping/i),
  ).toBeTruthy();
  expect(
    screen
      .getByRole('button', { name: 'Compile hypothesis' })
      .hasAttribute('disabled'),
  ).toBe(true);
  expect(fetcher).toHaveBeenCalledOnce();
});

it('does not confuse an unavailable research API with an empty workspace', async () => {
  const fetcher = vi.fn(async () => new Response('{}', { status: 503 }));
  mockApi(fetcher);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[]}
      mappingsState="unavailable"
      mappingsPartial={false}
    />,
  );
  await user.click(screen.getByText('Create research candidate'));
  expect(
    await screen.findByText(/Research records are unavailable/i),
  ).toBeTruthy();
  expect(screen.getByText(/Refresh the page before compiling/i)).toBeTruthy();
  expect(screen.queryByText('ProblemSpecs unavailable.')).toBeNull();
  expect(
    screen
      .getByRole('button', { name: 'Compile hypothesis' })
      .hasAttribute('disabled'),
  ).toBe(true);
});

it('restores a saved candidate from durable history without starting another check or run', async () => {
  const saved = candidate(
    '4',
    '4',
    'mix_positive_feature_maps',
    'hypothesis_changing',
    ['eq-a'],
    [{ name: 'nonzero_normalization_denominator', status: 'unresolved' }],
  );
  const history = {
    items: [
      {
        candidate: {
          ...saved.candidate,
          problem_spec_id: 'spec_0123456789abcdef',
          mapping_id: 'map-reviewed-1',
        },
        activity: saved.activity,
        check: {
          check_id: 'chk_' + 'c'.repeat(32),
          candidate_id: saved.candidate.candidate_id,
          checker_version: 'candidate-static.v3',
          claim: 'Candidate structure only.',
          scope: 'candidate_structure',
          assumptions: [],
          input_hashes: ['d'.repeat(64)],
          outcome: 'unknown',
          vector: { parse: 'supported', type: 'unknown', domain: 'unresolved' },
        },
        admission: admission(false).decision,
        numerical_fixture: numericalFixture(
          saved.candidate.candidate_id,
          'spec_0123456789abcdef',
          'counterexample',
        ),
      },
    ],
    total: 1,
  };
  const writes: string[] = [];
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (requestPath(input).startsWith('/api/research/problems?'))
        return Response.json(problemList);
      writes.push(requestPath(input));
      return new Response('{}', { status: 503 });
    },
  );
  mockApi(fetcher, history);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );

  await user.click(screen.getByText('Create research candidate'));
  const savedCandidate = await screen.findByRole('button', {
    name: /mix positive feature maps.*Check: unknown.*Policy recorded: denied/i,
  });
  await user.click(savedCandidate);

  expect(
    await screen.findByText(
      /No new check, admission decision, or run was started/i,
    ),
  ).toBeTruthy();
  expect(screen.getByText(/Policy recorded: denied/i)).toBeTruthy();
  expect(screen.getByText(/Fixture: counterexample/i)).toBeTruthy();
  await user.click(screen.getByText('Worker fixture · diagnostic only'));
  expect(screen.getByText('counterexample')).toBeTruthy();
  expect(screen.getByText(/does not run a paper implementation/i)).toBeTruthy();
  expect(screen.getByText('sha256:' + 'd'.repeat(64))).toBeTruthy();
  expect(
    screen.getAllByText(/Recorded 2026-09-25 04:00:00 UTC/i).length,
  ).toBeGreaterThan(0);
  expect(screen.getAllByText(/chk_c{32}/i).length).toBeGreaterThan(0);
  expect(writes).toEqual([]);
});

it('compares two saved candidates by scoped evidence without implying an empirical winner', async () => {
  const makeHistoryEntry = (
    id: string,
    operator: string,
    semantics: string,
    parentIds: string[],
    checkOutcome: string,
    domain: string,
    checkScope = 'candidate_structure',
  ) => {
    const saved = candidate(id, id, operator, semantics, parentIds, [
      { name: 'nonzero_normalization_denominator', status: 'conditional' },
    ]);
    return {
      candidate: {
        ...saved.candidate,
        problem_spec_id:
          id === '2' ? 'spec_fedcba9876543210' : 'spec_0123456789abcdef',
        mapping_id: 'map-reviewed-1',
      },
      activity: saved.activity,
      check: {
        check_id: `chk_${id.padStart(32, 'c')}`,
        candidate_id: saved.candidate.candidate_id,
        checker_version: 'candidate-static.v3',
        claim: 'Scoped candidate check.',
        scope: checkScope,
        assumptions: [],
        input_hashes: ['d'.repeat(64)],
        outcome: checkOutcome,
        vector: { parse: 'supported', type: 'unknown', domain },
      },
      admission: admission(false).decision,
      numerical_fixture: null,
    };
  };
  const history = {
    items: [
      makeHistoryEntry(
        '1',
        'mix_positive_feature_maps',
        'hypothesis_changing',
        ['eq-a', 'eq-b'],
        'unknown',
        'unresolved',
      ),
      makeHistoryEntry(
        '2',
        'lower_mixture_to_concatenation',
        'preserving',
        ['cand_parent'],
        'supported',
        'conditional',
        'feature_kernel_identity',
      ),
      makeHistoryEntry(
        '3',
        'mix_positive_feature_maps',
        'hypothesis_changing',
        ['eq-c', 'eq-d'],
        'unknown',
        'unresolved',
      ),
    ],
    total: 3,
  };
  const fetcher = vi.fn(async (input: RequestInfo | URL) =>
    requestPath(input).startsWith('/api/research/problems?')
      ? Response.json(problemList)
      : new Response('{}', { status: 503 }),
  );
  mockApi(fetcher, history);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );

  await user.click(screen.getByText('Create research candidate'));
  await screen.findByRole('button', {
    name: /Add mix positive feature maps candidate cand_0+1 to comparison/i,
  });
  await user.click(
    screen.getByRole('button', {
      name: /Add mix positive feature maps candidate cand_0+1 to comparison/i,
    }),
  );
  await user.click(
    screen.getByRole('button', {
      name: /Add lower mixture to concatenation candidate cand_0+2 to comparison/i,
    }),
  );

  const comparison = screen.getByRole('region', {
    name: 'Candidate comparison',
  });
  expect(
    screen.getByText(/Recorded structure and checks · no quality ranking/i),
  ).toBeTruthy();
  expect(screen.getByRole('note').textContent).toMatch(
    /Different frozen ProblemSpecs/,
  );
  expect(
    screen.getByRole('article', { name: 'Candidate 1' }).textContent,
  ).toContain('unresolved');
  expect(
    screen.getByRole('article', { name: 'Candidate 2' }).textContent,
  ).toContain('conditional');
  expect(comparison.textContent).toContain('EmpiricalNot run');
  expect(comparison.textContent).toContain('Scoped identity support');
  expect(comparison.textContent).toContain(
    'These records do not establish model quality, latency, memory, or a winning candidate.',
  );
  expect(
    screen
      .getByRole('button', {
        name: /Add mix positive feature maps candidate cand_0+3 to comparison/i,
      })
      .hasAttribute('disabled'),
  ).toBe(true);
  expect(
    fetcher.mock.calls.every(
      ([input]) =>
        requestPath(input).includes('/problems?') ||
        requestPath(input).includes('/candidates?'),
    ),
  ).toBe(true);
});

it('opens a scoped partial replay report only for the selected saved activity', async () => {
  const saved = candidate(
    '1', '1', 'mix_positive_feature_maps', 'hypothesis_changing', ['eq-a'], [],
  );
  const history = {
    items: [{
      candidate: {
        ...saved.candidate,
        problem_spec_id: 'spec_0123456789abcdef',
        mapping_id: 'map-reviewed-1',
      },
      activity: saved.activity,
      check: null,
      admission: null,
      numerical_fixture: null,
    }],
    total: 1,
  };
  const report = compilerReplayReport(
    saved.candidate.candidate_id,
    saved.activity.activity_id,
  );
  const correctBundle = {
    schema_version: 'compiler-replay-bundle.v1',
    bundle_hash: 'a'.repeat(64),
    candidate: { candidate_id: saved.candidate.candidate_id },
    activity: { activity_id: saved.activity.activity_id },
  };
  let bundleRequestCount = 0;
  let oversizedStreamCancelled = false;
  const oversizedBundle = new Response(
    new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(new Uint8Array(1024 * 1024));
      },
      cancel() {
        oversizedStreamCancelled = true;
      },
    }),
    { headers: { 'content-type': 'application/json' } },
  );
  let declaredOversizedStreamCancelled = false;
  const declaredOversizedBundle = new Response(
    new ReadableStream<Uint8Array>({
      cancel() {
        declaredOversizedStreamCancelled = true;
      },
    }),
    {
      headers: {
        'content-type': 'application/json',
        'content-length': String(4 * 1024 * 1024 + 1),
      },
    },
  );
  const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const path = requestPath(input);
    if (path.endsWith('/report')) return Response.json(report);
    if (path.includes('/bundle?')) {
      bundleRequestCount += 1;
      if (bundleRequestCount === 1) return Response.json(correctBundle);
      if (bundleRequestCount === 2) {
        return Response.json({ ...correctBundle, bundle_hash: 'b'.repeat(64) });
      }
      return bundleRequestCount === 3 ? oversizedBundle : declaredOversizedBundle;
    }
    return new Response('{}', { status: 503 });
  });
  mockApi(fetcher, history);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );

  await user.click(screen.getByText('Create research candidate'));
  await user.click(await screen.findByRole('button', { name: /mix positive feature maps.*No check/i }));
  const openReport = screen.getByRole('button', { name: 'Open replay report' });
  const reportRegionId = openReport.getAttribute('aria-controls');
  expect(reportRegionId).toBeTruthy();
  expect(document.getElementById(reportRegionId!)).toBeTruthy();
  expect(openReport.getAttribute('aria-expanded')).toBe('false');
  await user.click(openReport);

  expect(await screen.findByText(
    'Diagnostic replay report · empirical evaluation not run',
  )).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Refresh report' }).getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByText(/does not establish general mathematical correctness/i)).toBeTruthy();
  expect(screen.getByText(/Policy decisions · not mathematical verdicts/i)).toBeTruthy();
  expect(screen.getByText(/rerun from frozen policy input/i)).toBeTruthy();
  expect(screen.getByText(/Synthetic diagnostics · not empirical evidence/i)).toBeTruthy();
  expect(screen.getByText('Replayed; receipt matched')).toBeTruthy();
  expect(screen.getByText(/rerun from frozen input/i)).toBeTruthy();
  expect(screen.getByRole('link', { name: /Open HTML source/i }).getAttribute('href'))
    .toBe('https://arxiv.org/html/1706.03762v2#S3.E1');
  const reportCalls = fetcher.mock.calls.filter(([input]) => requestPath(input).endsWith('/report'));
  expect(reportCalls).toHaveLength(1);
  expect(requestPath(reportCalls[0]![0])).toBe(
    `/api/research/candidates/${saved.candidate.candidate_id}/activities/${saved.activity.activity_id}/report`,
  );
  expect(reportCalls[0]![1]).toMatchObject({ cache: 'no-store' });

  const createObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
  const revokeObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
  const createObjectURL = vi.fn(() => 'blob:replay-bundle');
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  try {
    await user.click(screen.getByRole('button', { name: 'Download replay bundle JSON' }));
    expect(await screen.findByText(/Bundle downloaded\. Verify it with/i)).toBeTruthy();
    expect(createObjectURL).toHaveBeenCalledOnce();
    const downloadedAnchor = anchorClick.mock.contexts[0] as HTMLAnchorElement;
    expect(downloadedAnchor.download).toBe(
      `fgl-${saved.candidate.candidate_id}-${saved.activity.activity_id}-replay-bundle.json`,
    );
    const bundleCall = fetcher.mock.calls.find(([input]) => requestPath(input).includes('/bundle?'));
    expect(bundleCall).toBeTruthy();
    expect(requestPath(bundleCall![0])).toBe(
      `/api/research/candidates/${saved.candidate.candidate_id}/activities/${saved.activity.activity_id}/bundle?bundle_hash=${'a'.repeat(64)}`,
    );
    expect(bundleCall![1]).toMatchObject({ cache: 'no-store' });

    await user.click(screen.getByRole('button', { name: 'Download bundle again' }));
    expect(await screen.findByText(/Bundle unavailable or changed since this report/i)).toBeTruthy();
    expect(createObjectURL).toHaveBeenCalledOnce();

    await user.click(screen.getByRole('button', { name: 'Download replay bundle JSON' }));
    expect(await screen.findByText(/Bundle unavailable or changed since this report/i)).toBeTruthy();
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(oversizedStreamCancelled).toBe(true);

    await user.click(screen.getByRole('button', { name: 'Download replay bundle JSON' }));
    expect(await screen.findByText(/Bundle unavailable or changed since this report/i)).toBeTruthy();
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(declaredOversizedStreamCancelled).toBe(true);
  } finally {
    if (createObjectURLDescriptor) {
      Object.defineProperty(URL, 'createObjectURL', createObjectURLDescriptor);
    } else {
      Reflect.deleteProperty(URL, 'createObjectURL');
    }
    if (revokeObjectURLDescriptor) {
      Object.defineProperty(URL, 'revokeObjectURL', revokeObjectURLDescriptor);
    } else {
      Reflect.deleteProperty(URL, 'revokeObjectURL');
    }
  }
});

it('rejects a replay report for another activity and offers a safe retry state', async () => {
  const saved = candidate(
    '2', '2', 'lower_mixture_to_concatenation', 'preserving', ['cand_parent'], [],
  );
  const history = {
    items: [{
      candidate: {
        ...saved.candidate,
        problem_spec_id: 'spec_0123456789abcdef',
        mapping_id: 'map-reviewed-1',
      },
      activity: saved.activity,
      check: null,
      admission: null,
      numerical_fixture: null,
    }],
    total: 1,
  };
  const wrongScope = compilerReplayReport(
    saved.candidate.candidate_id,
    `act_${'9'.repeat(32)}`,
  );
  const fetcher = vi.fn(async () => Response.json(wrongScope));
  mockApi(fetcher, history);
  const user = userEvent.setup();
  render(
    <ResearchMovePanel
      mappings={[mapping]}
      mappingsState="loaded"
      mappingsPartial={false}
    />,
  );

  await user.click(screen.getByText('Create research candidate'));
  await user.click(await screen.findByRole('button', { name: /lower mixture to concatenation.*No check/i }));
  await user.click(screen.getByRole('button', { name: 'Open replay report' }));

  expect(await screen.findByText(/Report unavailable or did not match this candidate/i)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Retry report' }).getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByRole('button', { name: 'Retry report' })).toBeTruthy();
  expect(screen.queryByText(
    'Diagnostic replay report · empirical evaluation not run',
  )).toBeNull();
});
