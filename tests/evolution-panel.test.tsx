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
