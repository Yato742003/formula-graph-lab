'use client';

import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  useNodesInitialized,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type OnSelectionChangeParams,
} from '@xyflow/react';
import katex from 'katex';
import {
  Compass,
  Maximize2,
  Minimize2,
  Orbit,
  RotateCcw,
  SlidersHorizontal,
  Waypoints,
} from 'lucide-react';
import { Suspense, lazy, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import type {
  EvidenceEntityType,
  EvidenceRelationType,
} from '@/lib/import-types';

const Graph3DViewport = lazy(() => import('@/app/graph-3d-viewport'));

export type GraphViewportNode = {
  id: string;
  kind: EvidenceEntityType;
  label: string;
  expression: string;
  meta: string;
};

export type GraphViewportEdge = {
  id: string;
  source: string;
  target: string;
  relation: EvidenceRelationType;
};

type EvidenceNodeData = GraphViewportNode & Record<string, unknown>;
type EvidenceFlowNode = Node<EvidenceNodeData, 'evidence'>;

type EvidenceGraphViewportProps = {
  nodes: GraphViewportNode[];
  edges: GraphViewportEdge[];
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
  loading?: boolean;
};

const kindColumns: Record<EvidenceEntityType, number> = {
  Paper: 0,
  PaperVersion: 1,
  Section: 2,
  Equation: 3,
  Symbol: 4,
  Assumption: 4,
  Claim: 4,
  Concept: 4,
  Method: 3,
  Experiment: 4,
  Hypothesis: 5,
};

const kindTone: Record<EvidenceEntityType, string> = {
  Paper: 'graph-node-paper',
  PaperVersion: 'graph-node-version',
  Section: 'graph-node-section',
  Equation: 'graph-node-equation',
  Symbol: 'graph-node-symbol',
  Assumption: 'graph-node-assumption',
  Claim: 'graph-node-claim',
  Concept: 'graph-node-concept',
  Method: 'graph-node-method',
  Experiment: 'graph-node-experiment',
  Hypothesis: 'graph-node-hypothesis',
};

const edgeColors: Record<EvidenceRelationType, string> = {
  has_version: '#5366d9',
  contains: '#71809a',
  defines: '#0f9d8a',
  uses: '#168aad',
  assumes: '#c17b22',
  cites: '#667085',
  makes_claim: '#8c5bc4',
  about: '#8c5bc4',
  derived_from: '#3764d8',
  approximates: '#d97706',
  generalizes: '#0d9488',
  equivalent_under: '#168aad',
  disagrees_with: '#d14343',
  supersedes: '#b14d76',
};

const minimapNodeColors: Record<EvidenceEntityType, string> = {
  Paper: '#38bdf8',
  PaperVersion: '#67e8f9',
  Section: '#64748b',
  Equation: '#10b981',
  Symbol: '#a855f7',
  Assumption: '#f43f5e',
  Claim: '#c084fc',
  Concept: '#34d399',
  Method: '#0ea5e9',
  Experiment: '#06b6d4',
  Hypothesis: '#f59e0b',
};

export function cleanFormula(expr: string): string {
  if (!expr) return expr;
  return expr
    .replace(/\\mathrm\{([^}]+)\}/g, '$1')
    .replace(/\\text\{([^}]+)\}/g, '$1')
    .replace(/\\displaystyle\s*/g, '')
    .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '($1 / $2)')
    .replace(/\\sqrt\{([^}]+)\}/g, '√($1)')
    .replace(/\\,/g, ' ')
    .replace(/~/g, ' ')
    .replace(/\\cdot/g, '·')
    .replace(/\\times/g, '×')
    .replace(/\\left|\\right/g, '')
    .trim();
}

export function renderFormulaHtml(rawExpr: string): string | null {
  if (!rawExpr || typeof rawExpr !== 'string') return null;
  const trimmed = rawExpr.trim();
  const clean = trimmed
    .replace(/^\${1,2}/, '')
    .replace(/\${1,2}$/, '')
    .trim();
  if (!clean) return null;
  try {
    return katex.renderToString(clean, {
      throwOnError: false,
      displayMode: false,
      strict: false,
    });
  } catch {
    return null;
  }
}

function EvidenceNodeCard({ data, selected }: NodeProps<EvidenceFlowNode>) {
  const isEquation = data.kind === 'Equation';
  const latexHtml = isEquation ? renderFormulaHtml(data.expression) : null;
  const displayExpression =
    isEquation ? cleanFormula(data.expression) : data.expression;

  return (
    <article
      className={`flow-evidence-node ${kindTone[data.kind]} ${selected ? 'is-selected' : ''}`}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <span>{data.kind}</span>
      <strong>{data.label}</strong>
      {latexHtml ? (
        <div
          className="node-latex-math"
          dangerouslySetInnerHTML={{ __html: latexHtml }}
        />
      ) : (
        <code>{displayExpression}</code>
      )}
      <small>{data.meta}</small>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </article>
  );
}

const MemoEvidenceNodeCard = memo(EvidenceNodeCard);
const nodeTypes = { evidence: MemoEvidenceNodeCard };

function relationLabel(relation: EvidenceRelationType): string {
  return relation.replaceAll('_', ' ');
}

function layoutNodes(
  nodes: GraphViewportNode[],
  edges: GraphViewportEdge[] = [],
): EvidenceFlowNode[] {
  // Build parent mapping for paper clustering
  const parentMap = new Map<string, string>();
  for (const edge of edges) {
    if (
      edge.relation === 'has_version' ||
      edge.relation === 'contains' ||
      edge.relation === 'defines' ||
      edge.relation === 'uses'
    ) {
      parentMap.set(edge.target, edge.source);
    }
  }

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  const findPaperRoot = (nodeId: string, depth = 0): string => {
    if (depth > 8) return 'standalone';
    const n = nodeMap.get(nodeId);
    if (!n) return 'standalone';
    if (n.kind === 'Paper' || n.kind === 'PaperVersion') return n.id;
    const parentId = parentMap.get(nodeId);
    if (parentId) return findPaperRoot(parentId, depth + 1);
    return 'standalone';
  };

  // Group nodes by paper cluster
  const clusters = new Map<string, GraphViewportNode[]>();
  for (const node of nodes) {
    const root = findPaperRoot(node.id);
    const list = clusters.get(root) ?? [];
    list.push(node);
    clusters.set(root, list);
  }

  const clusterEntries = [...clusters.entries()].sort(([aKey], [bKey]) => {
    if (aKey === 'standalone') return 1;
    if (bKey === 'standalone') return -1;
    return aKey.localeCompare(bKey);
  });

  let currentY = 36;
  const flowNodes: EvidenceFlowNode[] = [];

  const MAX_ROWS_PER_COL = 8;

  for (const [, clusterNodes] of clusterEntries) {
    const activeKindsInCluster = [...new Set(clusterNodes.map((node) => node.kind))].sort(
      (left, right) => (kindColumns[left] ?? 0) - (kindColumns[right] ?? 0),
    );

    // Compute column offsets per kind with multi-column wrapping for large node counts
    // ponytail: fixed grid packing layout, ceiling: 8 rows per sub-column, upgrade: container-height-aware packing if viewport height becomes user-resizable
    const kindBaseCol = new Map<EvidenceEntityType, number>();
    let totalClusterCols = 0;
    for (const kind of activeKindsInCluster) {
      kindBaseCol.set(kind, totalClusterCols);
      const count = clusterNodes.filter((node) => node.kind === kind).length;
      totalClusterCols += Math.max(1, Math.ceil(count / MAX_ROWS_PER_COL));
    }

    const kindItemCounters = new Map<EvidenceEntityType, number>();
    let maxRowInCluster = 1;

    const sortedNodes = [...clusterNodes].sort((left, right) => {
      const colDiff = (kindColumns[left.kind] ?? 0) - (kindColumns[right.kind] ?? 0);
      return (
        colDiff ||
        left.label.localeCompare(right.label) ||
        left.id.localeCompare(right.id)
      );
    });

    const colWidth = totalClusterCols <= 2 ? 340 : 380;
    const rowHeight = 150;
    const clusterGap = 48;

    for (const node of sortedNodes) {
      const baseCol = kindBaseCol.get(node.kind) ?? 0;
      const itemIdx = kindItemCounters.get(node.kind) ?? 0;
      kindItemCounters.set(node.kind, itemIdx + 1);

      const subCol = Math.floor(itemIdx / MAX_ROWS_PER_COL);
      const row = itemIdx % MAX_ROWS_PER_COL;
      const colIndex = baseCol + subCol;

      if (row + 1 > maxRowInCluster) maxRowInCluster = row + 1;

      flowNodes.push({
        id: node.id,
        type: 'evidence',
        position: { x: 36 + colIndex * colWidth, y: currentY + row * rowHeight },
        data: node,
        draggable: false,
        selectable: true,
        focusable: true,
        ariaRole: 'button',
        ariaLabel: `${node.kind}: ${node.label}. ${node.expression}`,
      });
    }

    currentY += maxRowInCluster * rowHeight + clusterGap;
  }

  return flowNodes;
}

function FitViewWatcher({
  nodeCount,
  filterKey,
}: {
  nodeCount: number;
  filterKey: string;
}) {
  const { fitView } = useReactFlow();
  const nodesInitialized = useNodesInitialized();
  const hasFittedRef = useRef(false);

  useEffect(() => {
    if (!nodesInitialized || nodeCount === 0) return;

    const timer = setTimeout(() => {
      void fitView({
        padding: 0.12,
        maxZoom: 1.05,
        minZoom: 0.25,
        duration: hasFittedRef.current ? 320 : 0,
      });
      hasFittedRef.current = true;
    }, 40);

    return () => clearTimeout(timer);
  }, [nodesInitialized, nodeCount, filterKey, fitView]);

  return null;
}

export default function EvidenceGraphViewport({
  nodes,
  edges,
  selectedId,
  onSelect,
  loading = false,
}: EvidenceGraphViewportProps) {
  const [viewMode, setViewMode] = useState<'2d' | '3d'>('2d');
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    if (!isFullscreen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsFullscreen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isFullscreen]);

  useEffect(() => {
    window.dispatchEvent(new Event('resize'));
  }, [isFullscreen]);
  const relations = useMemo(
    () => [...new Set(edges.map((edge) => edge.relation))].sort(),
    [edges],
  );
  const kinds = useMemo(
    () => [...new Set(nodes.map((node) => node.kind))].sort(),
    [nodes],
  );
  const [disabledRelations, setDisabledRelations] = useState<
    Set<EvidenceRelationType>
  >(() => new Set());
  // Focus view: defaults to Equations and Papers for clean mathematical lineage; user can toggle Sections/Symbols or switch to 3D Galaxy
  const [disabledKinds, setDisabledKinds] = useState<Set<EvidenceEntityType>>(() => {
    const hasEq = nodes.some((n) => n.kind === 'Equation');
    const hasSec = nodes.some((n) => n.kind === 'Section');
    if (hasEq && hasSec) {
      return new Set<EvidenceEntityType>(['Section', 'Symbol']);
    }
    return new Set<EvidenceEntityType>();
  });
  const activeFilterCount = disabledKinds.size + disabledRelations.size;

  const toggleKind = useCallback(
    (kind: EvidenceEntityType, checked: boolean) => {
      setDisabledKinds((current) => {
        const next = new Set(current);
        if (checked) next.delete(kind);
        else next.add(kind);
        return next;
      });
    },
    [],
  );

  const visibleNodes = useMemo(
    () => nodes.filter((node) => !disabledKinds.has(node.kind)),
    [nodes, disabledKinds],
  );

  const visibleNodeIds = useMemo(
    () => new Set(visibleNodes.map((n) => n.id)),
    [visibleNodes],
  );

  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [isMinimapOpen, setIsMinimapOpen] = useState(false);

  const activeFocusId = hoveredNodeId ?? selectedId;
  const activeEdgeIds = useMemo(() => {
    if (!activeFocusId) return null;
    const set = new Set<string>();
    for (const edge of edges) {
      if (edge.source === activeFocusId || edge.target === activeFocusId) {
        set.add(edge.id);
      }
    }
    return set;
  }, [activeFocusId, edges]);

  const visibleEdges = useMemo(
    () =>
      edges.filter(
        (edge) =>
          !disabledRelations.has(edge.relation) &&
          visibleNodeIds.has(edge.source) &&
          visibleNodeIds.has(edge.target),
      ),
    [edges, disabledRelations, visibleNodeIds],
  );

  const flowNodes = useMemo(
    () => layoutNodes(visibleNodes, edges),
    [visibleNodes, edges],
  );
  const flowEdges = useMemo<Edge[]>(
    () =>
      visibleEdges.map((edge) => {
        const isHierarchical =
          edge.relation === 'contains' ||
          edge.relation === 'defines' ||
          edge.relation === 'has_version' ||
          edge.relation === 'uses';
        const isHighlighted = activeEdgeIds !== null && activeEdgeIds.has(edge.id);
        const isDimmed = activeEdgeIds !== null && !isHighlighted;
        const stroke = edgeColors[edge.relation] ?? '#94a3b8';

        return {
          id: edge.id,
            source: edge.source,
            target: edge.target,
            label: isDimmed ? undefined : relationLabel(edge.relation),
            type: isHierarchical ? 'smoothstep' : 'default',
            pathOptions: isHierarchical ? { borderRadius: 16, offset: 20 } : undefined,
            selectable: true,
            focusable: true,
            animated: edge.relation === 'supersedes' || isHighlighted,
            zIndex: isHighlighted ? 20 : isDimmed ? 1 : 10,
            style: {
              stroke,
              strokeWidth: isHighlighted ? 2.5 : isDimmed ? 0.9 : 1.8,
              opacity: isDimmed ? 0.08 : 1.0,
              transition: 'opacity 0.2s ease, stroke-width 0.2s ease',
            },
            labelStyle: {
              fill: isHighlighted ? '#0f172a' : '#374151',
              fontSize: 10,
              fontWeight: isHighlighted ? 700 : 600,
            },
            labelBgStyle: {
              fill: '#ffffff',
              fillOpacity: isHighlighted ? 1.0 : 0.95,
              stroke: isHighlighted ? stroke : '#dce2ed',
              strokeWidth: isHighlighted ? 1.5 : 1,
            },
            labelBgPadding: [3, 7] as [number, number],
            labelBgBorderRadius: 6,
            ariaLabel: `${relationLabel(edge.relation)} relation`,
          };
        }),
    [visibleEdges, activeEdgeIds],
  );

  const handleNodeClick = useCallback(
    (_event: React.MouseEvent, node: EvidenceFlowNode) => onSelect(node.id),
    [onSelect],
  );
  const handleNodeMouseEnter = useCallback(
    (_event: React.MouseEvent, node: EvidenceFlowNode) => {
      setHoveredNodeId(node.id);
    },
    [],
  );
  const handleNodeMouseLeave = useCallback(() => {
    setHoveredNodeId(null);
  }, []);
  const handleSelectionChange = useCallback(
    ({ nodes: selectedNodes }: OnSelectionChangeParams<EvidenceFlowNode, Edge>) => {
      const selected = selectedNodes.at(-1);
      if (selected && selected.id !== selectedId) onSelect(selected.id);
    },
    [onSelect, selectedId],
  );
  const toggleRelation = useCallback(
    (relation: EvidenceRelationType, checked: boolean) => {
      setDisabledRelations((current) => {
        const next = new Set(current);
        if (checked) next.delete(relation);
        else next.add(relation);
        return next;
      });
    },
    [],
  );
  const handleGraphKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (!['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp'].includes(event.key)) {
        return;
      }
      if (event.target instanceof HTMLButtonElement) return;
      event.preventDefault();
      const currentIndex = nodes.findIndex((node) => node.id === selectedId);
      const direction =
        event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : -1;
      const start = currentIndex >= 0 ? currentIndex : direction > 0 ? -1 : 0;
      const nextIndex = (start + direction + nodes.length) % nodes.length;
      onSelect(nodes[nextIndex].id);
    },
    [nodes, onSelect, selectedId],
  );

  if (loading) {
    return (
      <output className="persisted-graph-loading">
        <span />
        <strong>Loading persisted evidence graph…</strong>
      </output>
    );
  }

  if (nodes.length === 0) {
    return (
      <div className="persisted-graph-empty">
        <strong>No persisted graph yet</strong>
        <span>Import an arXiv HTML paper to create the first evidence snapshot.</span>
      </div>
    );
  }

  return (
    <div
      className={`persisted-graph-shell ${isFullscreen ? 'is-fullscreen' : ''} ${viewMode === '3d' ? 'is-3d' : ''}`}
    >
      <div className="relation-toolbar" aria-label="Graph controls">
        <fieldset className="view-mode-toggle">
          <legend className="sr-only">Graph view</legend>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={`view-mode-btn ${viewMode === '2d' ? 'is-active' : ''}`}
            onClick={() => setViewMode('2d')}
            aria-pressed={viewMode === '2d'}
          >
            <Waypoints size={14} aria-hidden="true" />
            2D Map
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={`view-mode-btn ${viewMode === '3d' ? 'is-active-3d' : ''}`}
            onClick={() => setViewMode('3d')}
            aria-pressed={viewMode === '3d'}
          >
            <Orbit size={14} aria-hidden="true" />
            3D Space
          </Button>
        </fieldset>
        <output className="graph-scope" aria-live="polite">
          <strong>{visibleNodes.length}</strong> nodes
          <i aria-hidden="true" />
          <strong>{flowEdges.length}</strong> relations
        </output>
        <div className="graph-toolbar-spacer" />
        {/* ponytail: native disclosure avoids a popover dependency, ceiling: one non-nested filter panel, upgrade: anchored popover when filters gain commands */}
        <details className="graph-filter-menu">
          <summary>
            <SlidersHorizontal size={14} aria-hidden="true" />
            <span>Filters</span>
            {activeFilterCount > 0 ? (
              <b aria-label={`${activeFilterCount} active filters`}>
                {activeFilterCount}
              </b>
            ) : null}
          </summary>
          <div className="graph-filter-popover">
            {kinds.length > 1 ? (
              <fieldset>
                <legend>Entities</legend>
                <div className="graph-filter-options">
                  {kinds.map((kind) => (
                    <label key={kind}>
                      <Checkbox
                        checked={!disabledKinds.has(kind)}
                        onCheckedChange={(checked) =>
                          toggleKind(kind, checked === true)
                        }
                      />
                      {kind}
                    </label>
                  ))}
                </div>
              </fieldset>
            ) : null}
            <fieldset>
              <legend>Relations</legend>
              <div className="graph-filter-options">
                {relations.map((relation) => (
                  <label key={relation}>
                    <Checkbox
                      checked={!disabledRelations.has(relation)}
                      onCheckedChange={(checked) =>
                        toggleRelation(relation, checked === true)
                      }
                    />
                    <i style={{ background: edgeColors[relation] }} />
                    {relationLabel(relation)}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="graph-filter-footer">
              <span>
                {visibleNodes.length}/{nodes.length} nodes · {flowEdges.length}/{edges.length} relations
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setDisabledRelations(new Set());
                  setDisabledKinds(new Set());
                }}
                disabled={activeFilterCount === 0}
              >
                <RotateCcw size={13} aria-hidden="true" />
                Reset
              </Button>
            </div>
          </div>
        </details>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className={`fullscreen-toggle-btn ${isFullscreen ? 'is-active' : ''}`}
          onClick={() => setIsFullscreen((prev) => !prev)}
          title={isFullscreen ? 'Exit Fullscreen (Esc)' : 'Maximize Fullscreen'}
          aria-label={isFullscreen ? 'Exit Fullscreen' : 'Maximize Fullscreen'}
        >
          {isFullscreen ? (
            <Minimize2 size={13} className="mr-1" />
          ) : (
            <Maximize2 size={13} className="mr-1" />
          )}
          <span>{isFullscreen ? 'Exit' : 'Full screen'}</span>
        </Button>
      </div>

      <section
        className={`react-flow-stage ${viewMode === '3d' ? 'is-spatial' : ''}`}
        aria-label="Interactive evidence graph"
      >
        {viewMode === '3d' ? (
          <Suspense
            fallback={
              <div className="flex items-center justify-center w-full h-full min-h-0 text-slate-400 bg-[#020617]">
                Loading 3D Galaxy…
              </div>
            }
          >
            <Graph3DViewport
              nodes={visibleNodes}
              edges={visibleEdges}
              selectedId={selectedId}
              onSelect={onSelect}
              isFullscreen={isFullscreen}
            />
          </Suspense>
        ) : (
          <>
            <span className="sr-only" id="graph-keyboard-help">
              Use arrow keys to move selection between evidence nodes. Press Enter or
              Space on a focused node to select it.
            </span>
            <ReactFlow<EvidenceFlowNode, Edge>
              nodes={flowNodes}
              edges={flowEdges}
              nodeTypes={nodeTypes}
              onNodeClick={handleNodeClick}
              onNodeMouseEnter={handleNodeMouseEnter}
              onNodeMouseLeave={handleNodeMouseLeave}
              onSelectionChange={handleSelectionChange}
              nodesDraggable={false}
              nodesConnectable={false}
              nodesFocusable
              edgesFocusable
              elementsSelectable
              deleteKeyCode={null}
              minZoom={0.25}
              maxZoom={2}
              fitView
              fitViewOptions={{ padding: 0.12, maxZoom: 1.05 }}
              autoPanOnNodeFocus
              aria-describedby="graph-keyboard-help"
              onKeyDown={handleGraphKeyDown}
              tabIndex={0}
              ariaLabelConfig={{
                'node.a11yDescription.default':
                  'Press Enter or Space to select this evidence node.',
                'controls.ariaLabel': 'Graph zoom controls',
                'controls.zoomIn.ariaLabel': 'Zoom graph in',
                'controls.zoomOut.ariaLabel': 'Zoom graph out',
                'controls.fitView.ariaLabel': 'Fit all evidence in view',
                'minimap.ariaLabel': 'Evidence graph overview',
              }}
            >
              <FitViewWatcher
                nodeCount={flowNodes.length}
                filterKey={`${disabledKinds.size}-${disabledRelations.size}-${flowNodes.length}`}
              />
              <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
              <Panel position="bottom-right" className="minimap-panel">
                <button
                  type="button"
                  className="minimap-toggle-btn"
                  onClick={() => setIsMinimapOpen((prev) => !prev)}
                  aria-expanded={isMinimapOpen}
                  title={isMinimapOpen ? 'Collapse Overview MiniMap' : 'Expand Overview MiniMap'}
                >
                  <Compass size={13} className="shrink-0 text-cyan-500" />
                  <span>Overview</span>
                  <span className="minimap-toggle-arrow">{isMinimapOpen ? '▼' : '▲'}</span>
                </button>
                {isMinimapOpen ? (
                  <div className="minimap-container">
                    <MiniMap
                      className="evidence-minimap"
                      pannable
                      zoomable
                      ariaLabel="Evidence graph overview"
                      maskColor="rgba(15, 23, 42, 0.45)"
                      maskStrokeColor="#38bdf8"
                      maskStrokeWidth={2}
                      nodeBorderRadius={3}
                      nodeColor={(node) => {
                        const kind = (node.data as EvidenceNodeData).kind;
                        return minimapNodeColors[kind] ?? '#64748b';
                      }}
                    />
                  </div>
                ) : null}
              </Panel>
              <Controls showInteractive={false} />
            </ReactFlow>
          </>
        )}
      </section>

      <ol
        className={`mobile-graph-list ${viewMode === '3d' ? 'is-hidden' : ''}`}
        aria-label="Evidence nodes"
      >
        {visibleNodes.map((node) => (
          <li key={node.id}>
            <button
              type="button"
              aria-pressed={selectedId === node.id}
              onClick={() => onSelect(node.id)}
            >
              <span>{node.kind}</span>
              <strong>{node.label}</strong>
              <code>{node.expression}</code>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
