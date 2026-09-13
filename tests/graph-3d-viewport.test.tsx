// @vitest-environment jsdom

import './setup';

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Graph3DViewport from '../app/graph-3d-viewport';
import type { GraphViewportEdge, GraphViewportNode } from '../app/evidence-graph-viewport';

const rendererCreated = vi.fn();

vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();
  class MockWebGLRenderer {
    domElement = document.createElement('canvas');
    setSize = vi.fn();
    setPixelRatio = vi.fn();
    render = vi.fn();
    dispose = vi.fn();

    constructor() {
      rendererCreated();
    }
  }
  return {
    ...actual,
    WebGLRenderer: MockWebGLRenderer,
  };
});

const mockNodes: GraphViewportNode[] = [
  {
    id: 'eq1',
    kind: 'Equation',
    label: 'Scaled Dot-Product',
    expression: 'softmax(QK^T/sqrt(d_k))V',
    meta: '#S3.E1',
  },
];

const mockEdges: GraphViewportEdge[] = [];

beforeEach(() => rendererCreated.mockClear());
afterEach(() => cleanup());

describe('Graph3DViewport', () => {
  it('keeps primary camera controls visible and advanced controls fullscreen-only', () => {
    render(
      <Graph3DViewport
        nodes={mockNodes}
        edges={mockEdges}
        selectedId={null}
        onSelect={vi.fn()}
        isFullscreen={false}
      />,
    );

    expect(screen.getByText('3D lineage')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^full$/i })).toBeNull();

    expect(screen.getByRole('button', { name: /pause/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /reset/i })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /origin/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /overview/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /frontier/i })).toBeNull();
    expect(screen.queryByText(/timeline growth/i)).toBeNull();
  });

  it('shows roll, reset, presets and timeline growth controls in fullscreen mode', () => {
    render(
      <Graph3DViewport
        nodes={mockNodes}
        edges={mockEdges}
        selectedId={null}
        onSelect={vi.fn()}
        isFullscreen={true}
      />,
    );

    expect(screen.getByText('3D lineage')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^exit$/i })).toBeNull();

    expect(screen.getByRole('button', { name: /pause/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /reset/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /origin/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /overview/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /frontier/i })).toBeTruthy();
    expect(screen.getByText(/timeline growth/i)).toBeTruthy();
  });

  it('does not rebuild the WebGL scene when selection callbacks change', () => {
    const { rerender } = render(
      <Graph3DViewport
        nodes={mockNodes}
        edges={mockEdges}
        selectedId={null}
        onSelect={vi.fn()}
      />,
    );

    rerender(
      <Graph3DViewport
        nodes={mockNodes}
        edges={mockEdges}
        selectedId="eq1"
        onSelect={vi.fn()}
      />,
    );

    expect(rendererCreated).toHaveBeenCalledTimes(1);
  });

  it('lets researchers pause and resume automatic rotation', () => {
    render(
      <Graph3DViewport
        nodes={mockNodes}
        edges={mockEdges}
        selectedId={null}
        onSelect={vi.fn()}
      />,
    );

    const pause = screen.getByRole('button', { name: /pause/i });
    expect(pause.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(pause);
    expect(screen.getByRole('button', { name: /rotate/i }).getAttribute('aria-pressed')).toBe('false');
  });
});
