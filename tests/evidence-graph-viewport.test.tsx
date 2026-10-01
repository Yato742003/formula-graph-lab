// @vitest-environment jsdom

import './setup';

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EvidenceGraphViewport, {
  renderFormulaHtml,
  type GraphViewportEdge,
  type GraphViewportNode,
} from '../app/evidence-graph-viewport';

vi.mock('@/app/graph-3d-viewport', () => ({
  default: () => <div data-testid="mock-3d-viewport" />,
}));

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
    const { container } = render(
      <EvidenceGraphViewport
        nodes={nodes}
        edges={edges}
        selectedId="equation"
        onSelect={vi.fn()}
      />,
    );

    expect(container.querySelector('.graph-scope')?.textContent).toMatch(/2\s*relations/);
    await user.click(screen.getByText('Filters').closest('summary') as HTMLElement);
    await user.click(screen.getByRole('checkbox', { name: /contains/i }));
    expect(container.querySelector('.graph-scope')?.textContent).toMatch(/1\s*relations/);
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
      resolve(process.cwd(), 'app/globals.css'),
      'utf8',
    );

    expect(css).toMatch(/\.relation-toolbar\s*\{[^}]*flex-wrap:\s*wrap/);
    expect(css).toMatch(/\.flow-evidence-node strong\s*\{[^}]*font-size:\s*0\.9375rem/);
    expect(css).toMatch(/\.flow-evidence-node strong\s*\{[^}]*overflow-wrap:\s*anywhere/);
    expect(css).toMatch(/\.mobile-graph-list\s*\{[^}]*max-height:[^}]*overflow:\s*auto/);
  });

  it('filters entity kinds and updates visible node count', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <EvidenceGraphViewport
        nodes={nodes}
        edges={edges}
        selectedId="equation"
        onSelect={vi.fn()}
      />,
    );

    expect(container.querySelector('.graph-scope')?.textContent).toMatch(/3\s*nodes/);
    await user.click(screen.getByText('Filters').closest('summary') as HTMLElement);
    const symbolCheckbox = screen.getByRole('checkbox', { name: /^symbol$/i });
    await user.click(symbolCheckbox);
    expect(container.querySelector('.graph-scope')?.textContent).toMatch(/2\s*nodes/);
  });

  it('renders mathematical expressions with KaTeX HTML markup and styles', () => {
    const html = renderFormulaHtml('\\operatorname{Attention}(Q, K, V) = \\operatorname{softmax}\\left(\\frac{Q K^T}{\\sqrt{d_k}}\\right) V');
    expect(html).toContain('class="katex"');
    expect(html).toContain('Attention');

    const css = readFileSync(
      resolve(process.cwd(), 'app/globals.css'),
      'utf8',
    );
    expect(css).toMatch(/\.flow-evidence-node \.node-latex-math\s*\{[^}]*overflow-x:\s*auto/);
    expect(/\.flow-evidence-node\s*\{[^}]*width:\s*320px/.test(css)).toBe(true);
    expect(/\.flow-evidence-node\.is-compact\s*\{[^}]*width:\s*220px/.test(css)).toBe(true);
    // Detailed kind-specific dimensions must not override the compact layout.
    expect(css.includes('.flow-evidence-node.graph-node-equation:not(.is-compact)')).toBe(true);
  });

  it('provides a compact 2D Map / 3D Space view switcher', async () => {
    const user = userEvent.setup();
    render(
      <EvidenceGraphViewport
        nodes={nodes}
        edges={edges}
        selectedId="equation"
        onSelect={vi.fn()}
      />,
    );

    const btn2d = screen.getByRole('button', { name: /2D Map/i });
    const btn3d = screen.getByRole('button', { name: /3D Space/i });
    expect(btn2d.getAttribute('aria-pressed')).toBe('true');
    expect(btn3d.getAttribute('aria-pressed')).toBe('false');

    await user.click(btn3d);
    expect(btn3d.getAttribute('aria-pressed')).toBe('true');
    expect(btn2d.getAttribute('aria-pressed')).toBe('false');

    const css = readFileSync(
      resolve(process.cwd(), 'app/globals.css'),
      'utf8',
    );
    expect(css).toMatch(/\.view-mode-toggle\s*\{/);
    expect(css).toMatch(/\.view-mode-btn\.is-active-3d\s*\{/);
  });

  it('keeps advanced filters collapsed and exposes active filter count', async () => {
    const user = userEvent.setup();
    render(
      <EvidenceGraphViewport
        nodes={nodes}
        edges={edges}
        selectedId="equation"
        onSelect={vi.fn()}
      />,
    );

    const summary = screen.getByText('Filters').closest('summary') as HTMLElement;
    expect(summary.parentElement?.hasAttribute('open')).toBe(false);

    await user.click(summary);
    expect(summary.parentElement?.hasAttribute('open')).toBe(true);
    expect(screen.getByRole('checkbox', { name: /contains/i })).toBeTruthy();
    await user.click(screen.getByRole('checkbox', { name: /^symbol$/i }));
    expect(screen.getByLabelText('1 active filters')).toBeTruthy();
  });

  it('shows the spatial canvas instead of the mobile list in 3D mode', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <EvidenceGraphViewport
        nodes={nodes}
        edges={edges}
        selectedId="equation"
        onSelect={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: /3D Space/i }));
    expect(container.querySelector('.react-flow-stage')?.classList.contains('is-spatial')).toBe(true);
    expect(container.querySelector('.mobile-graph-list')?.classList.contains('is-hidden')).toBe(true);
  });

  it('wraps large entity lists into multiple sub-columns to eliminate empty whitespace', () => {
    const sectionNodes: GraphViewportNode[] = Array.from({ length: 24 }, (_, i) => ({
      id: `sec-${i}`,
      kind: 'Section',
      label: `Section ${i + 1}`,
      expression: `Content ${i}`,
      meta: `Sec ${i}`,
    }));
    const testNodes: GraphViewportNode[] = [
      { id: 'p1', kind: 'Paper', label: 'Paper 1', expression: 'arxiv', meta: '2026' },
      ...sectionNodes,
    ];
    const testEdges: GraphViewportEdge[] = sectionNodes.map((s) => ({
      id: `e-${s.id}`,
      source: 'p1',
      target: s.id,
      relation: 'contains',
    }));

    const { container } = render(
      <EvidenceGraphViewport
        nodes={testNodes}
        edges={testEdges}
        selectedId="p1"
        onSelect={vi.fn()}
      />,
    );

    // Bounded row height: With 24 sections wrapped at MAX_ROWS_PER_COL=8,
    // rows are 0..7 instead of 0..23, keeping layout compact and wide
    const renderedNodes = container.querySelectorAll('.flow-evidence-node');
    expect(renderedNodes.length).toBe(25);
  });

  it('does not log unknown edge type warnings to console', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(
      <EvidenceGraphViewport
        nodes={nodes}
        edges={edges}
        selectedId="equation"
        onSelect={vi.fn()}
      />,
    );
    const reactFlowEdgeWarnings = warnSpy.mock.calls.filter((call) =>
      call.some((arg) => typeof arg === 'string' && arg.includes('Edge type')),
    );
    expect(reactFlowEdgeWarnings).toHaveLength(0);
    warnSpy.mockRestore();
  });

  it('provides a collapsible MiniMap overview widget that toggles on click', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <EvidenceGraphViewport
        nodes={nodes}
        edges={edges}
        selectedId="equation"
        onSelect={vi.fn()}
      />,
    );

    const toggleBtn = screen.getByRole('button', { name: /Overview/i });
    expect(toggleBtn).toBeTruthy();
    expect(toggleBtn.getAttribute('aria-expanded')).toBe('false');
    expect(container.querySelector('.minimap-container')).toBeNull();

    await user.click(toggleBtn);
    expect(toggleBtn.getAttribute('aria-expanded')).toBe('true');
    expect(container.querySelector('.minimap-container')).toBeTruthy();
    expect(container.querySelector('.evidence-minimap')).toBeTruthy();

    await user.click(toggleBtn);
    expect(toggleBtn.getAttribute('aria-expanded')).toBe('false');
    expect(container.querySelector('.minimap-container')).toBeNull();
  });

  it('defines CSS styles for collapsible minimap', () => {
    const css = readFileSync(
      resolve(process.cwd(), 'app/globals.css'),
      'utf8',
    );
    expect(css).toMatch(/\.minimap-panel\s*\{/);
    expect(css).toMatch(/\.minimap-toggle-btn\s*\{/);
    expect(css).toMatch(/\.evidence-minimap\s*\{/);
  });

  it('toggles fullscreen mode with toolbar button and exits with Escape key', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <EvidenceGraphViewport
        nodes={nodes}
        edges={edges}
        selectedId="equation"
        onSelect={vi.fn()}
      />,
    );

    const shell = container.querySelector('.persisted-graph-shell');
    expect(shell).toBeTruthy();
    expect(shell?.classList.contains('is-fullscreen')).toBe(false);

    const fullBtn = screen.getByRole('button', { name: /maximize fullscreen/i });
    expect(fullBtn).toBeTruthy();

    // Enter fullscreen
    await user.click(fullBtn);
    expect(shell?.classList.contains('is-fullscreen')).toBe(true);

    // Exit button shown
    const exitBtn = screen.getByRole('button', { name: /exit fullscreen/i });
    expect(exitBtn).toBeTruthy();

    // Press Escape to exit
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(shell?.classList.contains('is-fullscreen')).toBe(false);
  });

  it('defines CSS styles for fullscreen graph shell and collapsible panels', () => {
    const css = readFileSync(
      resolve(process.cwd(), 'app/globals.css'),
      'utf8',
    );
    expect(css).toMatch(/\.persisted-graph-shell\.is-fullscreen\s*\{/);
    expect(css).toMatch(/\.fullscreen-toggle-btn\s*\{/);
    expect(css).toMatch(/\.research-grid\.is-left-collapsed\s*\{/);
    expect(css).toMatch(/\.research-grid\.is-right-collapsed\s*\{/);
    expect(css).toMatch(/\.source-panel\.is-collapsed/);
    expect(css).toMatch(/\.inspector-panel\.is-collapsed/);
  });
});
