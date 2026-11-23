// @vitest-environment jsdom
import './setup';
import { renderToString } from 'react-dom/server';
import { hydrateRoot } from 'react-dom/client';
import { act } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ResearchStagePage from '../app/research-stage-page';

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
  window.history.replaceState({}, '', '/');
  vi.unstubAllGlobals();
});

it('hydrates /reports?spec_id=... without SSR mismatch before mounting EvolutionPanel', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json({ items: [], total: 0, limit: 50, offset: 0 })),
  );

  const serverHtml = renderToString(
    <ResearchStagePage user={{ displayName: 'Seedy', email: 'seedy@sites.test' }} surface="reports" />,
  );
  expect(serverHtml).toContain('reports-evolution-empty');
  expect(serverHtml).not.toContain('Active Paper');
  expect(serverHtml).not.toContain('2609.30245');
  expect(serverHtml).not.toContain('1. Graph');

  window.history.replaceState({}, '', '/reports?spec_id=spec_123456');
  const container = document.createElement('div');
  container.innerHTML = serverHtml;
  document.body.appendChild(container);

  const onRecoverableError = vi.fn();
  let root: ReturnType<typeof hydrateRoot> | null = null;
  await act(async () => {
    root = hydrateRoot(
      container,
      <ResearchStagePage user={{ displayName: 'Seedy', email: 'seedy@sites.test' }} surface="reports" />,
      { onRecoverableError },
    );
  });

  expect(onRecoverableError).not.toHaveBeenCalled();
  expect(container.textContent).toContain('Evolution search (E1)');
  expect(container.textContent).not.toContain('Active Paper');
  expect(container.textContent).not.toContain('2609.30245');
  expect(container.textContent).not.toContain('1. Graph');
  expect(container.querySelectorAll('nav[aria-label="Research stages"]')).toHaveLength(1);

  await act(async () => {
    root?.unmount();
  });
});

it('renders /proposals with a single navigation rail and no hardcoded active paper banner', () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json({ items: [], total: 0, limit: 50, offset: 0 })),
  );

  const serverHtml = renderToString(
    <ResearchStagePage user={{ displayName: 'Seedy', email: 'seedy@sites.test' }} surface="proposals" />,
  );

  expect(serverHtml).not.toContain('Active Paper');
  expect(serverHtml).not.toContain('2609.30245');
  expect(serverHtml).not.toContain('1. Graph');
  expect(serverHtml.match(/aria-label="Research stages"/g)).toHaveLength(1);
});

it('lets users pick an existing frozen ProblemSpec directly inside /reports without dead-ending', async () => {
  window.history.replaceState({}, '', '/reports');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.includes('/api/research/problems')) {
        return Response.json({
          items: [
            {
              spec_id: 'spec_saved_1',
              campaign_id: 'cmp_1',
              version: 1,
              content_hash: 'a'.repeat(64),
              parent_spec_id: null,
              definition: { task: 'Compare linear attention feature maps' },
            },
          ],
          total: 1,
        });
      }
      return Response.json({ items: [], total: 0, limit: 50, offset: 0 });
    }),
  );

  render(<ResearchStagePage user={{ displayName: 'Seedy', email: 'seedy@sites.test' }} surface="reports" />);
  const picker = await screen.findByLabelText(/Or pick a saved spec now/i);
  fireEvent.change(picker, { target: { value: 'spec_saved_1' } });
  expect(await screen.findByText('Evolution search (E1)')).toBeTruthy();
  expect(window.location.search).toContain('spec_id=spec_saved_1');
});

it('shows prerequisite links on /proposals and avoids compiling copy on unavailable /reports', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 503 })));
  render(<ResearchStagePage user={{ displayName: 'Seedy', email: 'seedy@sites.test' }} surface="proposals" />);
  expect(screen.getByRole('link', { name: /Go to Spec/i }).getAttribute('href')).toBe('/spec');
  expect(screen.getByRole('link', { name: /Go to Compatibility/i }).getAttribute('href')).toBe('/compatibility');
  cleanup();

  render(<ResearchStagePage user={{ displayName: 'Seedy', email: 'seedy@sites.test' }} surface="reports" />);
  expect(await screen.findByText(/Saved history is unavailable/i)).toBeTruthy();
  expect(screen.queryByText(/before compiling/i)).toBeNull();
});
