// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EvidenceGraphViewport, {
  type GraphViewportEdge,
  type GraphViewportNode,
} from '../app/evidence-graph-viewport';

const nodes: GraphViewportNode[] = [
  {
    id: 'paper',
    kind: 'PaperVersion',
    label: 'Attention v7',
    expression: '1706.03762v7',
    meta: '2023-08-02',
  },
  {
    id: 'equation',
    kind: 'Equation',
    label: 'Equation 1',
    expression: 'softmax(QK^T)V',
    meta: '#S3.E1',
  },
  {
    id: 'symbol',
    kind: 'Symbol',
    label: 'Q',
    expression: 'query tensor',
    meta: 'n × d',
  },
];

const edges: GraphViewportEdge[] = [
  {
    id: 'contains',
    source: 'paper',
    target: 'equation',
    relation: 'contains',
  },
  {
    id: 'defines',
    source: 'equation',
    target: 'symbol',
    relation: 'defines',
  },
];

afterEach(() => cleanup());

describe('EvidenceGraphViewport', () => {
  it('moves the selected node with arrow-key navigation', () => {
    const onSelect = vi.fn();
    const { container } = render(
      <EvidenceGraphViewport
        nodes={nodes}
        edges={edges}
        selectedId="paper"
        onSelect={onSelect}
      />,
    );

    const flow = container.querySelector('.react-flow');
    expect(flow).toBeTruthy();
    fireEvent.keyDown(flow as Element, { key: 'ArrowRight' });
    expect(onSelect).toHaveBeenCalledWith('equation');
  });

  it('filters relations without removing evidence nodes', async () => {
    const user = userEvent.setup();
    render(
      <EvidenceGraphViewport
        nodes={nodes}
        edges={edges}
        selectedId="equation"
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByText('2 of 2 relations')).toBeTruthy();
    await user.click(screen.getByRole('checkbox', { name: /contains/i }));
    expect(screen.getByText('1 of 2 relations')).toBeTruthy();
    expect(screen.getByRole('list', { name: 'Evidence nodes' }).children).toHaveLength(3);
  });

  it('always renders a semantic small-screen node fallback', () => {
    render(
      <EvidenceGraphViewport
        nodes={nodes}
        edges={edges}
        selectedId="equation"
        onSelect={vi.fn()}
      />,
    );

    const fallback = screen.getByRole('list', { name: 'Evidence nodes' });
    expect(fallback).toBeTruthy();
    expect(
      screen
        .getByRole('button', { name: /Equation 1/i })
        .getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('keeps graph controls and labels usable at 200% text zoom', () => {
    const css = readFileSync(
      new URL('../app/globals.css', import.meta.url),
      'utf8',
    );

    expect(css).toMatch(/\.relation-toolbar\s*\{[^}]*flex-wrap:\s*wrap/s);
    expect(css).toMatch(/\.flow-evidence-node strong\s*\{[^}]*font-size:\s*0\.9rem/s);
    expect(css).toMatch(/\.flow-evidence-node strong\s*\{[^}]*overflow-wrap:\s*anywhere/s);
    expect(css).toMatch(/\.mobile-graph-list\s*\{[^}]*max-height:[^}]*overflow:\s*auto/s);
  });
});
