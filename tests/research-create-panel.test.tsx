// @vitest-environment jsdom
import './setup';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ResearchCreatePanel from '../app/research-create-panel';

const sources = ['a', 'b'].map(id => ({ id: `eq-${id}`, paper_id: `paper-${id}`, version: 1,
  latex: 'x=1', anchor: 'S1.E1', source_hash: id.repeat(64), symbols: ['x'] }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('source-backed record creation', () => {
  it.each(['vector', 'function'])('sends an append-only reviewed %s feature map without changing its category', async category => {
    const equationId = '11111111-1111-4111-8111-111111111111';
    const requests: RequestInit[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_input, init?: RequestInit) => {
      if (init?.method !== 'POST') return Response.json({
        items: [{ ...sources[0], id: equationId }], has_more: false,
      });
      requests.push(init);
      const body = JSON.parse(init.body as string);
      return Response.json({ ...body, review_id: 'review-saved', reviewer_id: 'human-1' });
    }));
    render(<ResearchCreatePanel onCreated={vi.fn()} />);
    fireEvent.click(screen.getByText('Create source-backed research records'));
    await screen.findByText('Source equations loaded.');
    fireEvent.change(screen.getByLabelText('Record type'), { target: { value: 'contract' } });
    fireEvent.change(screen.getByLabelText('Source / producer equation'), {
      target: { value: equationId },
    });
    fireEvent.change(screen.getByLabelText('Reviewed symbol'), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText('Category'), { target: { value: category } });
    if (category === 'function') fireEvent.change(screen.getByLabelText('Feature-map output domain'), {
      target: { value: 'strictly_positive_real' },
    });
    fireEvent.change(screen.getByLabelText('Domain'), { target: { value: 'real' } });
    fireEvent.change(screen.getByLabelText('Shape JSON'), { target: { value: '[128]' } });
    fireEvent.change(screen.getByLabelText('Feature-map output rank'), { target: { value: '128' } });
    fireEvent.change(screen.getByLabelText('Normalization'), { target: { value: 'none' } });
    fireEvent.change(screen.getByLabelText('Mask'), { target: { value: 'none' } });
    fireEvent.change(screen.getByLabelText('Causality'), { target: { value: 'no' } });
    fireEvent.change(screen.getByLabelText('Resource applicability'), {
      target: { value: 'not_applicable' },
    });
    fireEvent.change(screen.getByLabelText('Review rationale'), {
      target: { value: 'The source equation uses an unmasked scalar x.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save research record' }));
    await screen.findByText(/Record saved/);
    const payload = JSON.parse(requests[0].body as string);
    expect(payload.reviewed_contract).toMatchObject({
      name: 'x', category, domain: 'real', shape: [128], feature_rank: 128,
      normalization: 'none', mask: 'none', causal: false,
      resource_class: 'not_applicable',
    });
    expect(new Headers(requests[0].headers).get('idempotency-key')).toBeTruthy();
    expect(payload).not.toHaveProperty('review_id');
    expect(payload.reviewed_contract.feature_output_domain).toBe(category === 'function' ? 'strictly_positive_real' : undefined);
  });

  it('creates a paper citation with a section anchor and explicit direction', async () => {
    let sent: Record<string, unknown> | undefined;
    const papers = ['a', 'b'].map(id => ({
      id: `paper-version-${id}`, kind: 'paper', paper_id: `paper-${id}`, version: 1,
      latex: '', anchor: null, source_hash: id.repeat(64), symbols: [],
    }));
    const sections = [{
      id: 'section-b', kind: 'section', paper_id: 'paper-b', version: 1,
      latex: '', text: 'References: Paper A', anchor: 'Refs',
      source_hash: 'c'.repeat(64), symbols: [],
    }];
    vi.stubGlobal('fetch', vi.fn(async (input, init?: RequestInit) => {
      if (init?.method !== 'POST') return Response.json({
        items: String(input).includes('kind=paper') ? papers :
          String(input).includes('kind=section') ? sections : sources,
        has_more: false,
      });
      sent = JSON.parse(init.body as string);
      return Response.json({ ...sent, assertion_id: 'citation-saved', status: 'asserted' });
    }));
    render(<ResearchCreatePanel onCreated={vi.fn()} />);
    fireEvent.click(screen.getByText('Create source-backed research records'));
    await screen.findByText('Source equations loaded.');
    fireEvent.change(screen.getByLabelText('Relationship'), { target: { value: 'citation' } });
    expect(await screen.findAllByRole('option', { name: /paper-a v1/ })).toHaveLength(2);
    fireEvent.change(screen.getByLabelText('Cited predecessor paper'), {
      target: { value: 'paper-version-a' },
    });
    fireEvent.change(screen.getByLabelText('Citing descendant paper'), {
      target: { value: 'paper-version-b' },
    });
    fireEvent.change(screen.getByLabelText('Source section in the citing or revised paper'), {
      target: { value: 'section-b' },
    });
    fireEvent.change(screen.getByLabelText('Source rationale'), {
      target: { value: 'Paper B bibliography cites paper A.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save research record' }));
    await screen.findByText(/Record saved/);
    expect(sent?.source).toEqual({ kind: 'paper', id: 'paper-version-a', version: 1 });
    expect(sent?.target).toEqual({ kind: 'paper', id: 'paper-version-b', version: 1 });
    expect(sent?.evidence).toEqual([{
      source_entity_id: 'section-b', anchor: 'Refs', source_hash: 'c'.repeat(64),
    }]);
  });

  it('submits exact source references and does not self-review a lineage assertion', async () => {
    let sent: Record<string, unknown> | undefined;
    const onCreated = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async (_url, init?: RequestInit) => {
      if (init?.method !== 'POST') return Response.json({ items: sources, has_more: false });
      sent = JSON.parse(init.body as string);
      return Response.json({ ...sent, assertion_id: 'lineage-saved', status: 'asserted' });
    }));
    render(<ResearchCreatePanel onCreated={onCreated} />);
    fireEvent.click(screen.getByText('Create source-backed research records'));
    await screen.findByText('Source equations loaded.');
    fireEvent.change(screen.getByLabelText('Source / producer equation'), { target: { value: 'eq-a' } });
    fireEvent.change(screen.getByLabelText('Target / consumer equation'), { target: { value: 'eq-b' } });
    fireEvent.change(screen.getByLabelText('Source rationale'), { target: { value: 'See source equation context.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save research record' }));
    await screen.findByText(/Record saved/);
    expect(sent?.source).toEqual({ kind: 'equation', id: 'eq-a', version: 1 });
    expect(sent?.evidence).toEqual([{ source_entity_id: 'eq-a', anchor: 'S1.E1', source_hash: 'a'.repeat(64) }]);
    expect(sent).not.toHaveProperty('status');
    expect(onCreated).toHaveBeenCalledOnce();
  });

  it('sends only port references and refuses a malformed mapping receipt', async () => {
    let sent: Record<string, unknown> | undefined;
    const onCreated = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async (_url, init?: RequestInit) => {
      if (init?.method !== 'POST') return Response.json({ items: sources, has_more: false });
      sent = JSON.parse(init.body as string); return Response.json({ status: 'compatible' });
    }));
    render(<ResearchCreatePanel onCreated={onCreated} />);
    fireEvent.click(screen.getByText('Create source-backed research records'));
    await screen.findByText('Source equations loaded.');
    fireEvent.change(screen.getByLabelText('Record type'), { target: { value: 'compatibility' } });
    fireEvent.change(screen.getByLabelText('Source / producer equation'), { target: { value: 'eq-a' } });
    fireEvent.change(screen.getByLabelText('Target / consumer equation'), { target: { value: 'eq-b' } });
    fireEvent.change(screen.getByLabelText('Producer symbol'), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText('Consumer symbol'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save research record' }));
    await screen.findByText(/Not confirmed; inputs unchanged/);
    expect(sent?.mapping).toEqual({
      producer_port: { equation_id: 'eq-a', version: 1, symbol_name: 'x' },
      consumer_port: { equation_id: 'eq-b', version: 1, symbol_name: 'x' },
    });
    expect(onCreated).not.toHaveBeenCalled();
  });

  it('retries metadata-only creation with the same key without inventing HTML', async () => {
    const requests: RequestInit[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_url, init?: RequestInit) => {
      if (init?.method !== 'POST') return Response.json({ items: [], has_more: false });
      requests.push(init);
      if (requests.length === 1) return new Response('{}', { status: 503 });
      return Response.json({ paper_id: 'paper-c', has_html: false, equation_count: 0, registered_by: 'human-1' });
    }));
    render(<ResearchCreatePanel onCreated={vi.fn()} />);
    fireEvent.click(screen.getByText('Create source-backed research records'));
    fireEvent.change(screen.getByLabelText('Record type'), { target: { value: 'metadata' } });
    for (const [label, value] of [['Paper identifier', 'paper-c'], ['Paper title', 'Missing HTML'], ['Metadata source reference', 'Paper A bibliography']]) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.click(screen.getByRole('button', { name: 'Save research record' }));
    await screen.findByText(/Not confirmed/);
    fireEvent.click(screen.getByRole('button', { name: 'Save research record' }));
    await screen.findByText(/Record saved/);
    expect(new Headers(requests[0].headers).get('x-idempotency-key')).toBe(new Headers(requests[1].headers).get('x-idempotency-key'));
    expect(JSON.parse(requests[1].body as string)).not.toHaveProperty('has_html');
  });
});
