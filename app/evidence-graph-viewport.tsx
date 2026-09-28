'use client';

import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  useNodesInitialized,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import katex from 'katex';
import {
  Compass,
  Eye,
  LayoutGrid,
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

export type InCardSymbol = {
  id: string;
  label: string;
  expression: string;
};

type EvidenceNodeData = GraphViewportNode & {
  symbols?: InCardSymbol[];
  onSelectSymbol?: (id: string) => void;
  isCompact?: boolean;
  isDimmed?: boolean;
} & Record<string, unknown>;
export type EvidenceFlowNode = Node<EvidenceNodeData, 'evidence'>;

export type SectionGroupData = {
  title: string;
  count: number;
  sectionId?: string;
};
export type SectionGroupFlowNode = Node<SectionGroupData, 'sectionGroup'>;
export type AnyFlowNode = EvidenceFlowNode | SectionGroupFlowNode;

type EvidenceGraphViewportProps = {
  nodes: GraphViewportNode[];
  edges: GraphViewportEdge[];
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
  loading?: boolean;
  hasSnapshot?: boolean;
  isZenMode?: boolean;
  onToggleZen?: () => void;
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
  const isCompact = Boolean(data.isCompact);
  const latexHtml = isEquation && !isCompact ? renderFormulaHtml(data.expression) : null;
  const displayExpression =
    isEquation ? cleanFormula(data.expression) : data.expression;

  const isDimmed = Boolean(data.isDimmed);

  if (isCompact) {
    return (
      <article
        className={`flow-evidence-node is-compact ${kindTone[data.kind]} ${selected ? 'is-selected' : ''} ${isDimmed ? 'is-dimmed' : ''}`}
        title={`${data.kind}: ${data.label}\n${displayExpression}`}
      >
        <Handle id="top" type="target" position={Position.Top} isConnectable={false} />
        <Handle id="left" type="target" position={Position.Left} isConnectable={false} />
        <div className="compact-node-content">
          <span className="compact-node-badge">{data.kind.slice(0, 3)}</span>
          <strong className="compact-node-label truncate">{data.label}</strong>
          {data.symbols && data.symbols.length > 0 ? (
            <span
              className="compact-node-sym-count"
              title={`${data.symbols.length} defined symbols: ${data.symbols.map((s) => s.label).join(', ')}`}
            >
              {data.symbols.length}s
            </span>
          ) : null}
        </div>
        <Handle id="bottom" type="source" position={Position.Bottom} isConnectable={false} />
        <Handle id="right" type="source" position={Position.Right} isConnectable={false} />
      </article>
    );
  }

  return (
    <article
      className={`flow-evidence-node ${kindTone[data.kind]} ${selected ? 'is-selected' : ''} ${isDimmed ? 'is-dimmed' : ''}`}
    >
      <Handle id="top" type="target" position={Position.Top} isConnectable={false} />
      <Handle id="left" type="target" position={Position.Left} isConnectable={false} />
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
      {data.symbols && data.symbols.length > 0 ? (
        <div className="node-symbol-chips" aria-label="Defined symbols">
          <span className="symbol-chips-title">SYMBOLS</span>
          <div className="symbol-chips-list">
            {data.symbols.slice(0, 4).map((sym) => (
              <button
                key={sym.id}
                type="button"
                className="node-symbol-chip"
                onClick={(e) => {
                  e.stopPropagation();
                  data.onSelectSymbol?.(sym.id);
                }}
                title={`${sym.label}: ${sym.expression}`}
              >
                <span className="symbol-chip-sym">{sym.label}</span>
                {sym.expression ? (
                  <span className="symbol-chip-def">{sym.expression}</span>
                ) : null}
              </button>
            ))}
            {data.symbols.length > 4 ? (
              <span
                className="node-symbol-more"
                title={data.symbols.slice(4).map((s) => s.label).join(', ')}
              >
                +{data.symbols.length - 4}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
      <Handle id="bottom" type="source" position={Position.Bottom} isConnectable={false} />
      <Handle id="right" type="source" position={Position.Right} isConnectable={false} />
    </article>
  );
}

function SectionGroupCard({
  data,
}: NodeProps<SectionGroupFlowNode>) {
  return (
    <div className="section-swimlane-card" aria-hidden="true">
      <div className="section-swimlane-header">
        <div className="section-swimlane-title-group">
          <span className="section-swimlane-pill">SECTION</span>
          <strong className="section-swimlane-title" title={data.title}>
            {data.title}
          </strong>
        </div>
        {data.count > 0 ? (
          <span className="section-swimlane-badge">
            {data.count} {data.count === 1 ? 'formula' : 'formulas'}
          </span>
        ) : null}
      </div>
    </div>
  );
}

const MemoEvidenceNodeCard = memo(EvidenceNodeCard);
const MemoSectionGroupCard = memo(SectionGroupCard);

const nodeTypes = {
  evidence: MemoEvidenceNodeCard,
  sectionGroup: MemoSectionGroupCard,
};

function relationLabel(relation: EvidenceRelationType): string {
  return relation.replaceAll('_', ' ');
}

// Fallback multi-column wrapping grid for papers without equations or section-only test suites
function layoutGridFallback(
  nodes: GraphViewportNode[],
  edges: GraphViewportEdge[] = [],
  isCompact = false,
): AnyFlowNode[] {
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
  const flowNodes: AnyFlowNode[] = [];
  const MAX_ROWS_PER_COL = isCompact ? 10 : 8;

  for (const [, clusterNodes] of clusterEntries) {
    const activeKindsInCluster = [...new Set(clusterNodes.map((node) => node.kind))].sort(
      (left, right) => (kindColumns[left] ?? 0) - (kindColumns[right] ?? 0),
    );

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

    const colWidth = isCompact ? (totalClusterCols <= 2 ? 205 : 225) : (totalClusterCols <= 2 ? 340 : 380);
    const rowHeight = isCompact ? 58 : 150;
    const clusterGap = isCompact ? 28 : 48;

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
        data: { ...node, isCompact },
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

// Option 3: Section Swimlanes / Sub-flow Groups (Obsidian Canvas / Open Design)
function layoutNodes(
  nodes: GraphViewportNode[],
  edges: GraphViewportEdge[] = [],
  isCompact = false,
): AnyFlowNode[] {
  const hasEquations = nodes.some((n) => n.kind === 'Equation');
  if (!hasEquations) {
    return layoutGridFallback(nodes, edges, isCompact);
  }

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));

  // 1. Map equations to parent section
  const eqParentSection = new Map<string, string>();
  for (const edge of edges) {
    if (edge.relation === 'contains') {
      const src = nodeMap.get(edge.source);
      const tgt = nodeMap.get(edge.target);
      if (src?.kind === 'Section' && tgt?.kind === 'Equation') {
        eqParentSection.set(tgt.id, src.id);
      }
    }
  }

  // 2. Map symbols defined by equations
  const eqDefinedSymbols = new Map<string, GraphViewportNode[]>();
  for (const edge of edges) {
    if (edge.relation === 'defines') {
      const src = nodeMap.get(edge.source);
      const tgt = nodeMap.get(edge.target);
      if (src?.kind === 'Equation' && tgt?.kind === 'Symbol') {
        const list = eqDefinedSymbols.get(src.id) ?? [];
        list.push(tgt);
        eqDefinedSymbols.set(src.id, list);
      }
    }
  }

  // 3. Group equations by Section
  const sectionEquations = new Map<string, GraphViewportNode[]>();
  for (const node of nodes) {
    if (node.kind === 'Equation') {
      let secId = eqParentSection.get(node.id);
      if (!secId && node.meta) {
        const secMatch = node.meta.match(/#?(S\d+)/i);
        if (secMatch) {
          const matchedSec = nodes.find(
            (n) => n.kind === 'Section' && (n.id.includes(secMatch[1]) || n.label.includes(secMatch[1]))
          );
          if (matchedSec) secId = matchedSec.id;
        }
      }
      secId = secId ?? 'core-derivations';
      const list = sectionEquations.get(secId) ?? [];
      list.push(node);
      sectionEquations.set(secId, list);
    }
  }

  // Order sections by natural appearance in nodes
  const orderedSections = [...sectionEquations.keys()].sort((a, b) => {
    if (a === 'core-derivations') return 1;
    if (b === 'core-derivations') return -1;
    const idxA = nodes.findIndex((n) => n.id === a);
    const idxB = nodes.findIndex((n) => n.id === b);
    return idxA - idxB;
  });

  const flowNodes: AnyFlowNode[] = [];
  const assignedNodeIds = new Set<string>();

  const cardWidth = isCompact ? 205 : 280;
  const cardHeight = isCompact ? 58 : 150;
  const cardGap = isCompact ? 14 : 22;
  const swimlanePadX = 18;
  const swimlanePadTop = 64;
  const swimlanePadBottom = 22;
  const swimlaneGap = isCompact ? 36 : 56;

  const rootNodes = nodes.filter((n) => n.kind === 'Paper' || n.kind === 'PaperVersion');
  const hasPaperRoot = rootNodes.length > 0;
  const paperColWidth = isCompact ? 205 : 250;
  const laneInnerWidth = cardWidth;
  const laneTotalWidth = laneInnerWidth + swimlanePadX * 2;

  // Maximum columns per row to maintain a balanced 16:9 ratio and prevent canvas stretching
  const MAX_LANES_PER_ROW = 4;
  const row0Lanes = hasPaperRoot ? 3 : MAX_LANES_PER_ROW;

  const getSectionGridPos = (idx: number) => {
    if (hasPaperRoot) {
      if (idx < row0Lanes) {
        return { row: 0, col: idx };
      }
      const adj = idx - row0Lanes;
      return {
        row: 1 + Math.floor(adj / MAX_LANES_PER_ROW),
        col: adj % MAX_LANES_PER_ROW,
      };
    }
    return {
      row: Math.floor(idx / MAX_LANES_PER_ROW),
      col: idx % MAX_LANES_PER_ROW,
    };
  };

  // Precalculate height for each row
  const rowMaxHeights = new Map<number, number>();
  orderedSections.forEach((secId, idx) => {
    const eqs = sectionEquations.get(secId) ?? [];
    const laneTotalHeight = swimlanePadTop + eqs.length * (cardHeight + cardGap) + swimlanePadBottom;
    const { row } = getSectionGridPos(idx);
    const currMax = rowMaxHeights.get(row) ?? 0;
    if (laneTotalHeight > currMax) {
      rowMaxHeights.set(row, laneTotalHeight);
    }
  });

  // Calculate rowStartY for each row
  const totalRows = orderedSections.length > 0 ? getSectionGridPos(orderedSections.length - 1).row + 1 : 0;
  const rowStartY = new Map<number, number>();
  let cumY = 40;
  for (let r = 0; r < Math.max(1, totalRows); r++) {
    rowStartY.set(r, cumY);
    cumY += (rowMaxHeights.get(r) ?? 380) + swimlaneGap;
  }

  // 4. Preceding Column: Paper / PaperVersion node (anchor at left of Row 0)
  if (hasPaperRoot) {
    rootNodes.forEach((node, idx) => {
      assignedNodeIds.add(node.id);
      flowNodes.push({
        id: node.id,
        type: 'evidence',
        position: { x: 36, y: (rowStartY.get(0) ?? 40) + idx * (cardHeight + cardGap) },
        data: { ...node, isCompact: true },
        draggable: false,
        selectable: true,
        focusable: true,
        ariaRole: 'button',
        ariaLabel: `${node.kind}: ${node.label}. ${node.expression}`,
      });
    });
  }

  // 5. Section Swimlanes for Equations & Embedded Symbol Chips
  orderedSections.forEach((secId, idx) => {
    const eqs = sectionEquations.get(secId) ?? [];
    const secNode = nodeMap.get(secId);
    let title = 'Core Derivations';
    if (secNode) {
      title = secNode.label || secNode.expression || 'Section';
      assignedNodeIds.add(secNode.id);
    }

    const { row, col } = getSectionGridPos(idx);
    const startY = rowStartY.get(row) ?? 40;
    const xBase = 36 + (row === 0 && hasPaperRoot ? paperColWidth + swimlaneGap : 0);
    const startX = xBase + col * (laneTotalWidth + swimlaneGap);

    eqs.forEach((eq, curRow) => {
      assignedNodeIds.add(eq.id);
      const syms = (eqDefinedSymbols.get(eq.id) ?? []).filter((s) => nodeMap.has(s.id));
      syms.forEach((s) => assignedNodeIds.add(s.id));

      const inCardSymbols: InCardSymbol[] = syms.map((s) => ({
        id: s.id,
        label: s.label,
        expression: s.expression,
      }));

      const eqY = startY + swimlanePadTop + curRow * (cardHeight + cardGap);
      flowNodes.push({
        id: eq.id,
        type: 'evidence',
        position: {
          x: startX + swimlanePadX,
          y: eqY,
        },
        data: {
          ...eq,
          isCompact,
          symbols: inCardSymbols,
        },
        draggable: false,
        selectable: true,
        focusable: true,
        ariaRole: 'button',
        ariaLabel: `${eq.kind}: ${eq.label}. ${eq.expression}`,
      });
    });

    const laneTotalHeight = swimlanePadTop + eqs.length * (cardHeight + cardGap) + swimlanePadBottom;

    flowNodes.unshift({
      id: `swimlane-${secId}`,
      type: 'sectionGroup',
      position: { x: startX, y: startY },
      style: { width: laneTotalWidth, height: laneTotalHeight },
      data: {
        title,
        count: eqs.length,
        sectionId: secId,
      },
      draggable: false,
      selectable: false,
      focusable: false,
      zIndex: -1,
    } as AnyFlowNode);
  });

  // Rule 11 (ui-craft): Empty sections earn no canvas space.
  // They are fully browsable in the Left Sidebar ("Extracted Structure").
  for (const node of nodes) {
    if (node.kind === 'Section') {
      assignedNodeIds.add(node.id);
    }
  }

  // 6. Remaining unassigned nodes (Assumptions, Claims, Hypotheses, etc.)
  const remainingNodes = nodes.filter((n) => !assignedNodeIds.has(n.id));
  if (remainingNodes.length > 0) {
    let maxX = 36;
    for (const fn of flowNodes) {
      const w = fn.type === 'sectionGroup' ? ((fn.style?.width as number) || laneTotalWidth) : cardWidth;
      if (fn.position.x + w > maxX) {
        maxX = fn.position.x + w;
      }
    }
    const rightClusterX = maxX + swimlaneGap;
    remainingNodes.forEach((node, idx) => {
      const y = 40 + idx * (cardHeight + cardGap);
      flowNodes.push({
        id: node.id,
        type: 'evidence',
        position: { x: rightClusterX, y },
        data: { ...node, isCompact },
        draggable: false,
        selectable: true,
        focusable: true,
        ariaRole: 'button',
        ariaLabel: `${node.kind}: ${node.label}. ${node.expression}`,
      });
    });
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
      const stage = typeof document !== 'undefined' ? document.querySelector('.react-flow-stage') : null;
      if (stage && (stage.clientWidth === 0 || stage.clientHeight === 0)) return;

      void fitView({
        padding: 0.12,
        maxZoom: 1.05,
        minZoom: 0.25,
        duration: hasFittedRef.current ? 300 : 0,
      });
      hasFittedRef.current = true;
    }, 200);

    return () => clearTimeout(timer);
  }, [nodesInitialized, nodeCount, filterKey, fitView]);

  useEffect(() => {
    let resizeTimer: ReturnType<typeof setTimeout>;
    const handleResize = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        const stage = typeof document !== 'undefined' ? document.querySelector('.react-flow-stage') : null;
        if (stage && (stage.clientWidth === 0 || stage.clientHeight === 0)) return;

        void fitView({
          padding: 0.12,
          maxZoom: 1.05,
          minZoom: 0.25,
          duration: 250,
        });
      }, 150);
    };
    window.addEventListener('resize', handleResize);
    return () => {
      clearTimeout(resizeTimer);
      window.removeEventListener('resize', handleResize);
    };
  }, [fitView]);

  return null;
}

export default function EvidenceGraphViewport({
  nodes,
  edges,
  selectedId,
  onSelect,
  loading = false,
  hasSnapshot = false,
  isZenMode = false,
  onToggleZen,
}: EvidenceGraphViewportProps) {
  const [viewMode, setViewMode] = useState<'2d' | '3d'>('2d');
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isCompactMode, setIsCompactMode] = useState(false);

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
    const rafId = requestAnimationFrame(() => {
      window.dispatchEvent(new Event('resize'));
    });
    return () => cancelAnimationFrame(rafId);
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
  const [disabledKinds, setDisabledKinds] = useState<Set<EvidenceEntityType>>(
    () => new Set<EvidenceEntityType>(),
  );
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
  const activeNeighborNodeIds = useMemo(() => {
    if (!activeFocusId) return null;
    const set = new Set<string>([activeFocusId]);
    for (const edge of edges) {
      if (edge.source === activeFocusId) set.add(edge.target);
      if (edge.target === activeFocusId) set.add(edge.source);
    }
    return set;
  }, [activeFocusId, edges]);

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

  const baseLayoutNodes = useMemo(
    () => layoutNodes(visibleNodes, edges, isCompactMode),
    [visibleNodes, edges, isCompactMode],
  );

  const flowNodes = useMemo(
    () =>
      baseLayoutNodes.map((node) => {
        if (node.type === 'sectionGroup') {
          return node;
        }
        const isSelected = node.id === selectedId;
        const isDimmed =
          activeNeighborNodeIds !== null &&
          !activeNeighborNodeIds.has(node.id);
        if (
          node.selected === isSelected &&
          Boolean(node.data.isDimmed) === isDimmed &&
          node.data.onSelectSymbol === onSelect
        ) {
          return node;
        }
        return {
          ...node,
          selected: isSelected,
          data: {
            ...node.data,
            isDimmed,
            onSelectSymbol: onSelect,
          },
        };
      }),
    [baseLayoutNodes, selectedId, activeNeighborNodeIds, onSelect],
  );
  const flowEdges = useMemo<Edge[]>(() => {
    const isSwimlaneLayout = baseLayoutNodes.some((n) => n.type === 'sectionGroup');
    const nodePosMap = new Map<string, { x: number; y: number }>();
    const nodeKindMap = new Map<string, EvidenceEntityType>();

    for (const fn of baseLayoutNodes) {
      if (fn.type !== 'sectionGroup') {
        nodePosMap.set(fn.id, fn.position);
        nodeKindMap.set(fn.id, (fn.data as EvidenceNodeData).kind);
      }
    }

    const resultEdges: Edge[] = [];
    const connectedPairs = new Set<string>();

    for (const edge of visibleEdges) {
      const srcPos = nodePosMap.get(edge.source);
      const tgtPos = nodePosMap.get(edge.target);

      // If either node is not rendered on canvas (e.g. empty section or in-card symbol),
      // omit canvas edge to prevent dangling lines or React Flow warnings.
      if (!srcPos || !tgtPos) {
        continue;
      }

      const isContainment = edge.relation === 'contains' || edge.relation === 'has_version';
      const isHighlighted = activeEdgeIds !== null && activeEdgeIds.has(edge.id);
      const isDimmed = activeEdgeIds !== null && !isHighlighted;

      // In Section Swimlanes view, structural containment edges (paper->section, section->eq)
      // are represented by the physical swimlane containers.
      // Omit unhighlighted containment edges to eliminate 76+ crossing wires!
      if (isSwimlaneLayout && isContainment && !isHighlighted) {
        continue;
      }

      connectedPairs.add(`${edge.source}->${edge.target}`);

      const stroke = edgeColors[edge.relation] ?? '#94a3b8';
      let sourceHandle: string | undefined = undefined;
      let targetHandle: string | undefined = undefined;
      let edgeType: string = 'default';

      if (srcPos && tgtPos) {
        const dx = tgtPos.x - srcPos.x;
        const dy = tgtPos.y - srcPos.y;

        if (edge.relation === 'defines' || nodeKindMap.get(edge.target) === 'Symbol') {
          sourceHandle = 'right';
          targetHandle = 'left';
          edgeType = 'smoothstep';
        } else if (Math.abs(dx) < 60) {
          if (dy >= 0) {
            sourceHandle = 'bottom';
            targetHandle = 'top';
          } else {
            sourceHandle = 'top';
            targetHandle = 'bottom';
          }
          edgeType = 'smoothstep';
        } else if (dx > 0) {
          sourceHandle = 'right';
          targetHandle = 'left';
          edgeType = 'default';
        } else {
          sourceHandle = 'left';
          targetHandle = 'right';
          edgeType = 'default';
        }
      }

      resultEdges.push({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle,
        targetHandle,
        type: edgeType,
        label: isHighlighted ? relationLabel(edge.relation) : undefined,
        markerEnd: isContainment
          ? undefined
          : {
              type: MarkerType.ArrowClosed,
              color: stroke,
              width: 14,
              height: 14,
            },
        selectable: true,
        focusable: true,
        animated: edge.relation === 'supersedes' || (isHighlighted && !isContainment),
        zIndex: isHighlighted ? 20 : isDimmed ? 1 : 10,
        style: {
          stroke,
          strokeWidth: isHighlighted ? 2.5 : isDimmed ? 0.9 : 1.8,
          strokeDasharray: isContainment ? '4 4' : undefined,
          opacity: isDimmed ? 0.08 : isContainment ? 0.5 : 1.0,
          transition: 'opacity 0.2s ease, stroke-width 0.2s ease',
        },
        labelStyle: {
          fill: '#0f172a',
          fontSize: 10,
          fontWeight: 700,
        },
        labelBgStyle: {
          fill: '#ffffff',
          fillOpacity: 1.0,
          stroke,
          strokeWidth: 1.5,
        },
        labelBgPadding: [3, 7] as [number, number],
        labelBgBorderRadius: 6,
        ariaLabel: `${relationLabel(edge.relation)} relation`,
      });
    }

    // In Section Swimlanes view: Synthesize the clean intra-section and cross-section
    // derivation flow matching Option Section Swimlanes
    if (isSwimlaneLayout) {
      const secEqMap = new Map<string, string[]>();
      for (const fn of baseLayoutNodes) {
        if (fn.type === 'sectionGroup') {
          const secId = (fn.data as SectionGroupData).sectionId;
          if (secId) secEqMap.set(secId, []);
        }
      }
      for (const fn of baseLayoutNodes) {
        if (fn.type === 'evidence' && (fn.data as EvidenceNodeData).kind === 'Equation') {
          for (const sg of baseLayoutNodes) {
            if (sg.type === 'sectionGroup') {
              const secId = (sg.data as SectionGroupData).sectionId;
              const sgW = (sg.style?.width as number) || 300;
              if (secId && fn.position.x >= sg.position.x && fn.position.x < sg.position.x + sgW) {
                const list = secEqMap.get(secId) ?? [];
                list.push(fn.id);
                secEqMap.set(secId, list);
                break;
              }
            }
          }
        }
      }

      const activeSectionIds = [...secEqMap.keys()].filter(
        (sid) => (secEqMap.get(sid)?.length ?? 0) > 0,
      );
      const derivationColor = '#059669'; // Emerald derivation line

      // Intra-swimlane sequential derivation: Eq 0 -> Eq 1 -> Eq 2 (vertical straight down)
      activeSectionIds.forEach((secId) => {
        const eqIds = secEqMap.get(secId) ?? [];
        for (let i = 0; i < eqIds.length - 1; i++) {
          const srcId = eqIds[i];
          const tgtId = eqIds[i + 1];
          const pairKey = `${srcId}->${tgtId}`;
          const revKey = `${tgtId}->${srcId}`;
          if (!connectedPairs.has(pairKey) && !connectedPairs.has(revKey)) {
            connectedPairs.add(pairKey);
            const edgeId = `swimlane-seq-${srcId}-${tgtId}`;
            const isHighlighted =
              activeEdgeIds !== null &&
              (activeEdgeIds.has(srcId) || activeEdgeIds.has(tgtId));
            const isDimmed = activeEdgeIds !== null && !isHighlighted;

            resultEdges.push({
              id: edgeId,
              source: srcId,
              target: tgtId,
              sourceHandle: 'bottom',
              targetHandle: 'top',
              type: 'smoothstep',
              markerEnd: {
                type: MarkerType.ArrowClosed,
                color: derivationColor,
                width: 14,
                height: 14,
              },
              selectable: true,
              focusable: true,
              zIndex: isHighlighted ? 20 : isDimmed ? 1 : 10,
              style: {
                stroke: derivationColor,
                strokeWidth: isHighlighted ? 2.5 : isDimmed ? 0.9 : 2.0,
                opacity: isDimmed ? 0.08 : 0.95,
                transition: 'opacity 0.2s ease, stroke-width 0.2s ease',
              },
              ariaLabel: 'intra-section derivation',
            });
          }
        }
      });

      // Cross-swimlane pipeline bridge: last Eq of Section K -> first Eq of Section K+1 (smooth horizontal bezier)
      for (let s = 0; s < activeSectionIds.length - 1; s++) {
        const currEqs = secEqMap.get(activeSectionIds[s]) ?? [];
        const nextEqs = secEqMap.get(activeSectionIds[s + 1]) ?? [];
        if (currEqs.length > 0 && nextEqs.length > 0) {
          const lastEqId = currEqs[currEqs.length - 1];
          const firstNextEqId = nextEqs[0];
          const bridgeKey = `${lastEqId}->${firstNextEqId}`;
          if (!connectedPairs.has(bridgeKey)) {
            connectedPairs.add(bridgeKey);
            const edgeId = `swimlane-bridge-${lastEqId}-${firstNextEqId}`;
            const bridgeColor = '#0d9488'; // Teal
            const isHighlighted =
              activeEdgeIds !== null &&
              (activeEdgeIds.has(lastEqId) || activeEdgeIds.has(firstNextEqId));
            const isDimmed = activeEdgeIds !== null && !isHighlighted;

            const lastPos = nodePosMap.get(lastEqId);
            const nextPos = nodePosMap.get(firstNextEqId);
            const isRowWrap = Boolean(lastPos && nextPos && nextPos.x < lastPos.x);

            resultEdges.push({
              id: edgeId,
              source: lastEqId,
              target: firstNextEqId,
              sourceHandle: isRowWrap ? 'bottom' : 'right',
              targetHandle: isRowWrap ? 'top' : 'left',
              type: isRowWrap ? 'smoothstep' : 'default',
              markerEnd: {
                type: MarkerType.ArrowClosed,
                color: bridgeColor,
                width: 15,
                height: 15,
              },
              selectable: true,
              focusable: true,
              zIndex: isHighlighted ? 20 : isDimmed ? 1 : 12,
              style: {
                stroke: bridgeColor,
                strokeWidth: isHighlighted ? 2.8 : isDimmed ? 0.9 : 2.2,
                opacity: isDimmed ? 0.08 : 0.95,
                transition: 'opacity 0.2s ease, stroke-width 0.2s ease',
              },
              ariaLabel: 'cross-section derivation bridge',
            });
          }
        }
      }
    }

    return resultEdges;
  }, [baseLayoutNodes, visibleEdges, activeEdgeIds]);

  const handleNodeClick = useCallback(
    (_event: React.MouseEvent, node: AnyFlowNode) => {
      if (node.type === 'sectionGroup') {
        const secId = (node as SectionGroupFlowNode).data?.sectionId;
        if (secId) onSelect(secId);
        return;
      }
      onSelect(node.id);
    },
    [onSelect],
  );

  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleNodeMouseEnter = useCallback(
    (_event: React.MouseEvent, node: AnyFlowNode) => {
      if (node.type === 'sectionGroup') return;
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = setTimeout(() => {
        setHoveredNodeId(node.id);
      }, 50);
    },
    [],
  );
  const handleNodeMouseLeave = useCallback(() => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    hoverTimerRef.current = setTimeout(() => {
      setHoveredNodeId(null);
    }, 50);
  }, []);

  useEffect(() => {
    return () => {
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    };
  }, []);
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
        <strong>{hasSnapshot ? 'No equation evidence in this snapshot' : 'No persisted graph yet'}</strong>
        <span>{hasSnapshot
          ? 'This paper has no extracted equations. Its source and lineage records remain available.'
          : 'Import an arXiv HTML paper to create the first evidence snapshot.'}</span>
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
          <strong>{visibleEdges.length}</strong> relations
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
                    <label key={kind} htmlFor={`filter-kind-${kind}`}>
                      <Checkbox
                        id={`filter-kind-${kind}`}
                        name={`filter_kind_${kind}`}
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
                  <label key={relation} htmlFor={`filter-rel-${relation}`}>
                    <Checkbox
                      id={`filter-rel-${relation}`}
                      name={`filter_rel_${relation}`}
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
                {visibleNodes.length}/{nodes.length} nodes · {visibleEdges.length}/{edges.length} relations
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
          className={`density-toggle-btn ${isCompactMode ? 'is-active' : ''}`}
          onClick={() => setIsCompactMode((prev) => !prev)}
          title={isCompactMode ? 'Switch to Detailed Formula Cards' : 'Switch to Compact Nodes (Fits 72 nodes in view)'}
          aria-label={isCompactMode ? 'Detailed Cards' : 'Compact Nodes'}
        >
          <LayoutGrid size={13} className="mr-1 text-indigo-500" />
          <span>{isCompactMode ? 'Compact' : 'Detailed'}</span>
        </Button>
        {onToggleZen ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={`zen-toggle-btn ${isZenMode ? 'is-active' : ''}`}
            onClick={onToggleZen}
            title={isZenMode ? 'Exit Zen Mode (Press Z)' : 'Zen Focus Mode: Hide Sidebars (Press Z)'}
            aria-label={isZenMode ? 'Exit Zen Mode' : 'Zen Focus Mode'}
          >
            <Eye size={13} className="mr-1 text-sky-500" />
            <span>{isZenMode ? 'Exit Zen' : 'Zen (Z)'}</span>
          </Button>
        ) : null}
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
            <ReactFlow<AnyFlowNode, Edge>
              nodes={flowNodes}
              edges={flowEdges}
              nodeTypes={nodeTypes}
              onNodeClick={handleNodeClick}
              onPaneClick={() => onSelect('')}
              onNodeMouseEnter={handleNodeMouseEnter}
              onNodeMouseLeave={handleNodeMouseLeave}
              nodesDraggable={false}
              nodesConnectable={false}
              nodesFocusable
              edgesFocusable
              elementsSelectable
              deleteKeyCode={null}
              minZoom={0.25}
              maxZoom={2}
              defaultViewport={{ x: 0, y: 0, zoom: 1 }}
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
                filterKey={`${disabledKinds.size}-${disabledRelations.size}-${flowNodes.length}-${isCompactMode ? 'c' : 'd'}-${isZenMode ? 'z' : 'n'}`}
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
              <Controls position="top-left" showInteractive={false} />
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
