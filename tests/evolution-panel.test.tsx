// @vitest-environment jsdom
import './setup';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import EvolutionPanel, { parseEvolutionCampaign } from '../app/evolution-panel';

const campaign = {
  evolution_id: `evo_${'a'.repeat(32)}`,
  spec: { spec_id: 'spec-one' },
  status: 'active',
  search_stop_reason: 'none',
  stop_reason: 'none',
  generation: 0,
  evaluations: [],
  pareto_archive: [],
  finalist_ids: [],
  winner_ids: [],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('opens a scoped ledger without sending metrics or evidence', async () => {
  const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'POST') {
      return Response.json({ campaign, replayed: false }, { status: 201 });
    }
    return Response.json({ items: [], total: 0, limit: 50, offset: 0 });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<EvolutionPanel specId="spec-one" />);

  const open = await screen.findByRole('button', { name: 'Open bounded campaign' });
  fireEvent.click(open);
  expect(await screen.findByText(/Campaign ledger opened/)).toBeTruthy();
  const call = fetcher.mock.calls.find(([, init]) => init?.method === 'POST');
  expect(call).toBeDefined();
  const requestBody = call?.[1]?.body;
  if (typeof requestBody !== 'string') throw new Error('Expected a JSON request body');
  const body = JSON.parse(requestBody);
  expect(body).toEqual({ spec_id: 'spec-one' });
  expect(body).not.toHaveProperty('metrics');
  expect(body).not.toHaveProperty('fitness');
  expect(screen.getByText(/empirical=not_run/)).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Open campaign report' }).getAttribute('href'))
    .toBe(`/api/research/evolution/${campaign.evolution_id}/report`);
});

it('loads and stops the exact campaign while preserving trust labels', async () => {
  const stopped = { ...campaign, status: 'stopped', stop_reason: 'user_stop' };
  const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
    init?.method === 'POST'
      ? Response.json({ campaign: stopped, replayed: false })
      : Response.json({ items: [campaign], total: 1, limit: 50, offset: 0 }),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<EvolutionPanel specId="spec-one" />);

  fireEvent.click(await screen.findByRole('button', { name: 'Stop campaign' }));
  expect(await screen.findByText(/Campaign stopped/)).toBeTruthy();
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop campaign' })).toBeNull());
  const stopCall = fetcher.mock.calls.find(([, init]) => init?.method === 'POST');
  expect(stopCall?.[0]).toBe(`/api/research/evolution/${campaign.evolution_id}/stop`);
  expect(stopCall?.[1]?.body).toBe('{}');
});

it('rejects malformed campaign history instead of rendering promoted state', () => {
  expect(() => parseEvolutionCampaign({
    ...campaign,
    winner_ids: [123],
  })).toThrow(/Invalid evolution campaign/);
});

it('runs one generation, freezes explicit finalists, then confirms without client fitness', async () => {
  const candidateId = `cand_${'b'.repeat(32)}`;
  const bodies: { action: string; body: unknown }[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method !== 'POST') return Response.json({ items: [campaign], total: 1 });
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const action = path.split('/').at(-1) ?? '';
    if (typeof init.body !== 'string') throw new Error('Expected JSON body');
    bodies.push({ action, body: JSON.parse(init.body) });
    return Response.json({ campaign: { ...campaign, generation: 1,
      status: action === 'generation' ? 'search_stopped' : action === 'finalists' ? 'finalists_frozen' : 'stopped',
      search_stop_reason: 'candidate_budget_exhausted', stop_reason: action === 'confirm' ? 'confirmation_completed' : 'none',
      evaluations: [{}], pareto_archive: [candidateId],
      finalist_ids: action === 'generation' ? [] : [candidateId], winner_ids: [],
    }, replayed: false });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<EvolutionPanel specId="spec-one" />);
  fireEvent.change(await screen.findByLabelText('Reviewed search seed'), { target: { value: candidateId } });
  fireEvent.click(screen.getByRole('button', { name: 'Run next generation' }));
  expect(await screen.findByText(/Generation recorded from server receipts/)).toBeTruthy();
  fireEvent.click(screen.getByText('Choose finalists (0)'));
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Freeze selected finalists' }));
  expect(await screen.findByText(/Finalists frozen/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Run next generation' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm on locked holdout' }));
  expect(await screen.findByText(/Confirmation recorded/)).toBeTruthy();
  expect(bodies).toEqual([
    { action: 'generation', body: { seed_candidate_id: candidateId } },
    { action: 'finalists', body: { finalist_ids: [candidateId] } },
    { action: 'confirm', body: {} },
  ]);
  expect(screen.getByRole('link', { name: 'Export campaign bundle' })).toBeTruthy();
});
