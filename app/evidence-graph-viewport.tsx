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
import {
  Compass,
  Cpu,
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
import {
  cleanFormula,
  renderFormulaHtml,
  type GraphViewportEdge,
  type GraphViewportNode,
  type InCardSymbol,
} from '@/lib/graph-formula-render';
import {
  computeLayout2DAsync,
  computeLayout2DFallback,
  type Layout2DInput,
  type Layout2DResult,
  type LayoutEngineKind,
} from '@/lib/graph-layout-wasm';
import type {
  EvidenceEntityType,
  EvidenceRelationType,
} from '@/lib/import-types';

export {
  cleanFormula,
  renderFormulaHtml,
  type GraphViewportEdge,
  type GraphViewportNode,
  type InCardSymbol,
};

const prefetch3DViewport = () => {
  void import('@/app/graph-3d-viewport');
};

const Graph3DViewport = lazy(() => import('@/app/graph-3d-viewport'));

type LaneTone = 'paper' | 'section' | 'equation' | 'concept' | 'hypothesis';

type EvidenceNodeData = GraphViewportNode & {
  symbols?: InCardSymbol[];
  onSelectSymbol?: (id: string) => void;
  isCompact?: boolean;
  isDimmed?: boolean;
  sectionId?: string;
} & Record<string, unknown>;
export type EvidenceFlowNode = Node<EvidenceNodeData, 'evidence'>;

export type SectionGroupData = {
  title: string;
  pill: string;
  unitLabel: string;
  tone: LaneTone;
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

const kindRankOrder: Record<EvidenceEntityType, number> = {
  Paper: 0,
  PaperVersion: 1,
  Section: 2,
  Equation: 3,
  Method: 4,
  Concept: 5,
  Assumption: 6,
  Experiment: 7,
  Symbol: 8,
  Claim: 9,
  Hypothesis: 10,
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
  Paper: '#3b82f6',
  PaperVersion: '#0ea5e9',
  Section: '#0f9d8a',
  Equation: '#4f46e5',
  Symbol: '#9333ea',
  Assumption: '#d97706',
  Claim: '#c026d3',
  Concept: '#10b981',
  Method: '#0284c7',
  Experiment: '#0891b2',
  Hypothesis: '#d97706',
};

function estimateCardWidth(kind: EvidenceEntityType, isCompact: boolean): number {
  if (isCompact) return 220;
  if (kind === 'Paper' || kind === 'PaperVersion') return 280;
  return 320;
}

function estimateCardHeight(
  node: GraphViewportNode,
  symbols: InCardSymbol[],
  isCompact: boolean,
): number {
  if (isCompact) return 48;

  // Base vertical budget: padding + kind eyebrow + title + meta row.
  let height = 108;
  const labelLines = Math.max(1, Math.min(3, Math.ceil(node.label.length / 30)));
  height += (labelLines - 1) * 21;

  if (node.kind === 'Equation') {
    const expr = node.expression ?? '';
    const hasTallMath = /\\(frac|dfrac|sum|int|prod|begin|sqrt|lim|binom|matrix)/.test(expr);
    const hasVeryLongMath = expr.length > 85;
    height += hasTallMath ? 68 : hasVeryLongMath ? 50 : 38;
  } else {
    const exprLen = (node.expression ?? '').length;
    const exprLines = Math.max(1, Math.min(4, Math.ceil(exprLen / 36)));
    height += exprLines * 21 + 8;
  }

  if (symbols.length > 0) {
    const shown = symbols.slice(0, 4);
    let rowW = 0;
    let chipRows = 1;
    const maxRowW = 264;
    for (const sym of shown) {
      const defWidth = sym.expression ? Math.min(114, sym.expression.length * 6.2) : 0;
      const chipW = Math.min(maxRowW, 24 + sym.label.length * 7.2 + defWidth);
      if (rowW > 0 && rowW + 5 + chipW > maxRowW) {
        chipRows++;
        rowW = chipW;
      } else {
        rowW += (rowW > 0 ? 5 : 0) + chipW;
      }
    }
    if (symbols.length > 4 && rowW + 32 > maxRowW) {
      chipRows++;
    }
    height += 36 + chipRows * 29;
  }

  return Math.max(130, height);
}

type LaneSpec = {
  key: string;
  title: string;
  pill: string;
  unitLabel: string;
  tone: LaneTone;
  sectionId?: string;
  maxCols: number;
  allowBarycenter: boolean;
  members: Array<{
    node: GraphViewportNode;
    symbols: InCardSymbol[];
    forceCompact?: boolean;
  }>;
};

type Prepared2DModel = {
  lanes: LaneSpec[];
  flatItems: Array<{
    node: GraphViewportNode;
    symbols: InCardSymbol[];
    forceCompact: boolean;
    laneIndex: number;
    sectionId?: string;
  }>;
  input: Layout2DInput;
};

function prepareUnified2DModel(
  nodes: GraphViewportNode[],
  edges: GraphViewportEdge[],
  isCompact: boolean,
): Prepared2DModel {
  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  const hasEquations = nodes.some((n) => n.kind === 'Equation');

  // Map equations to parent section via `contains` edges or `#S\d+` anchor metadata.
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

  // Map symbols defined by equations so they embed as clean in-card chips.
  const eqDefinedSymbols = new Map<string, GraphViewportNode[]>();
  for (const edge of edges) {
    if (edge.relation === 'defines') {
      const src = nodeMap.get(edge.source);
      const tgt = nodeMap.get(edge.target);
      if (src?.kind === 'Equation' && tgt?.kind === 'Symbol') {
        const list = eqDefinedSymbols.get(src.id) ?? [];
        if (!list.some((existing) => existing.id === tgt.id)) {
          list.push(tgt);
        }
        eqDefinedSymbols.set(src.id, list);
      }
    }
  }

  const lanes: LaneSpec[] = [];
  const assignedIds = new Set<string>();

  // Lane 0 (always index 0): Source Paper anchor lane.
  const rootNodes = nodes.filter(
    (n) => n.kind === 'Paper' || n.kind === 'PaperVersion',
  );
  rootNodes.forEach((n) => assignedIds.add(n.id));
  lanes.push({
    key: 'paper-root',
    title: rootNodes.length === 1 ? rootNodes[0].label : 'Source Paper',
    pill: 'SOURCE',
    unitLabel: rootNodes.length === 1 ? 'paper' : 'papers',
    tone: 'paper',
    sectionId: rootNodes[0]?.id ?? 'paper-root',
    maxCols: 1,
    allowBarycenter: false,
    members: rootNodes.map((node) => ({
      node,
      symbols: [],
      forceCompact: rootNodes.length > 2,
    })),
  });

  if (hasEquations) {
    // Group equations into their parent sections (or Core Derivations if unattached).
    const sectionEquations = new Map<string, GraphViewportNode[]>();
    for (const node of nodes) {
      if (node.kind !== 'Equation') continue;
      let secId = eqParentSection.get(node.id);
      if (!secId && node.meta) {
        const secMatch = node.meta.match(/#?(S\d+)/i);
        if (secMatch) {
          const matchedSec = nodes.find(
            (n) =>
              n.kind === 'Section' &&
              (n.id.includes(secMatch[1]) || n.label.includes(secMatch[1])),
          );
          if (matchedSec) secId = matchedSec.id;
        }
      }
      secId = secId ?? 'core-derivations';
      const list = sectionEquations.get(secId) ?? [];
      list.push(node);
      sectionEquations.set(secId, list);
    }

    const orderedSectionIds = [...sectionEquations.keys()].sort((a, b) => {
      if (a === 'core-derivations') return 1;
      if (b === 'core-derivations') return -1;
      return (
        nodes.findIndex((n) => n.id === a) - nodes.findIndex((n) => n.id === b)
      );
    });

    for (const secId of orderedSectionIds) {
      const eqs = sectionEquations.get(secId) ?? [];
      if (eqs.length === 0) continue;
      const secNode = nodeMap.get(secId);
      if (secNode) assignedIds.add(secNode.id);

      const members = eqs.map((eq) => {
        assignedIds.add(eq.id);
        const syms = (eqDefinedSymbols.get(eq.id) ?? []).filter((s) =>
          nodeMap.has(s.id),
        );
        syms.forEach((s) => assignedIds.add(s.id));
        return {
          node: eq,
          symbols: syms.map((s) => ({
            id: s.id,
            label: s.label,
            expression: s.expression,
          })),
        };
      });

      const title = secNode
        ? secNode.label || secNode.expression || 'Section'
        : 'Core Derivations';
      const maxCols = eqs.length > 12 ? 3 : eqs.length > 6 ? 2 : 1;

      lanes.push({
        key: secId,
        title,
        pill: secNode ? 'SECTION' : 'DERIVATIONS',
        unitLabel: eqs.length === 1 ? 'formula' : 'formulas',
        tone: secNode ? 'section' : 'equation',
        sectionId: secId,
        maxCols,
        allowBarycenter: false,
        members,
      });
    }

    // Empty section nodes stay accessible in the left structure outline, not as empty canvas boxes.
    for (const node of nodes) {
      if (node.kind === 'Section') assignedIds.add(node.id);
    }
  } else {
    // No equations present: keep Section nodes visible in a unified Document Sections lane
    // folded cleanly across up to 3 sub-columns so large section outlines stay compact.
    const sectionNodes = nodes.filter(
      (n) => n.kind === 'Section' && !assignedIds.has(n.id),
    );
    if (sectionNodes.length > 0) {
      sectionNodes.forEach((n) => assignedIds.add(n.id));
      lanes.push({
        key: 'document-sections',
        title: 'Document Sections',
        pill: 'OUTLINE',
        unitLabel: sectionNodes.length === 1 ? 'section' : 'sections',
        tone: 'section',
        sectionId: 'document-sections',
        maxCols: sectionNodes.length > 6 ? 3 : sectionNodes.length > 3 ? 2 : 1,
        allowBarycenter: false,
        members: sectionNodes.map((node) => ({
          node,
          symbols: [],
          forceCompact: sectionNodes.length > 10,
        })),
      });
    }
  }

  // Group remaining non-equation entities into two clean epistemic lanes:
  // 1) Concepts, Methods, Assumptions, Experiments & standalone Symbols
  // 2) Claims & AI Hypotheses (explicitly separated from immutable evidence!)
  const remaining = nodes.filter((n) => !assignedIds.has(n.id));
  const contextEntities = remaining.filter(
    (n) => n.kind !== 'Claim' && n.kind !== 'Hypothesis',
  );
  const epistemicEntities = remaining.filter(
    (n) => n.kind === 'Claim' || n.kind === 'Hypothesis',
  );

  if (contextEntities.length > 0) {
    lanes.push({
      key: 'context-entities',
      title: 'Concepts & Assumptions',
      pill: 'CONTEXT',
      unitLabel: contextEntities.length === 1 ? 'entity' : 'entities',
      tone: 'concept',
      sectionId: 'context-entities',
      maxCols: contextEntities.length > 8 ? 2 : 1,
      allowBarycenter: true,
      members: contextEntities.map((node) => ({ node, symbols: [] })),
    });
  }

  if (epistemicEntities.length > 0) {
    lanes.push({
      key: 'epistemic-hypotheses',
      title: 'Claims & Hypotheses',
      pill: 'HYPOTHESIS',
      unitLabel: epistemicEntities.length === 1 ? 'item' : 'items',
      tone: 'hypothesis',
      sectionId: 'epistemic-hypotheses',
      maxCols: epistemicEntities.length > 8 ? 2 : 1,
      allowBarycenter: true,
      members: epistemicEntities.map((node) => ({ node, symbols: [] })),
    });
  }

  // Flatten items and pack typed arrays for Rust WASM layout engine.
  const flatItems: Prepared2DModel['flatItems'] = [];
  const nodeIndexById = new Map<string, number>();

  lanes.forEach((lane, laneIndex) => {
    lane.members.forEach((member) => {
      const idx = flatItems.length;
      nodeIndexById.set(member.node.id, idx);
      flatItems.push({
        node: member.node,
        symbols: member.symbols,
        forceCompact: Boolean(member.forceCompact),
        laneIndex,
        sectionId: lane.sectionId,
      });
    });
  });

  const n = flatItems.length;
  const g = lanes.length;
  const nodeGroupIds = new Uint32Array(n);
  const nodeKindRanks = new Uint32Array(n);
  const nodeWidths = new Float32Array(n);
  const nodeHeights = new Float32Array(n);

  for (let i = 0; i < n; i++) {
    const item = flatItems[i];
    const effectiveCompact = isCompact || item.forceCompact;
    nodeGroupIds[i] = item.laneIndex;
    nodeKindRanks[i] = kindRankOrder[item.node.kind] ?? 5;
    nodeWidths[i] = estimateCardWidth(item.node.kind, effectiveCompact);
    nodeHeights[i] = estimateCardHeight(
      item.node,
      item.symbols,
      effectiveCompact,
    );
  }

  const edgeSrcList: number[] = [];
  const edgeDstList: number[] = [];
  const edgeWeightList: number[] = [];
  for (const edge of edges) {
    const u = nodeIndexById.get(edge.source);
    const v = nodeIndexById.get(edge.target);
    if (u !== undefined && v !== undefined && u !== v) {
      edgeSrcList.push(u);
      edgeDstList.push(v);
      edgeWeightList.push(
        edge.relation === 'derived_from' || edge.relation === 'uses' ? 2.5 : 1.0,
      );
    }
  }

  const groupMaxCols = new Uint32Array(g);
  const groupBaryFlags = new Uint32Array(g);
  for (let laneIdx = 0; laneIdx < g; laneIdx++) {
    groupMaxCols[laneIdx] = lanes[laneIdx].maxCols;
    groupBaryFlags[laneIdx] = lanes[laneIdx].allowBarycenter ? 1 : 0;
  }

  const nonEmptyContentLanes = lanes
    .slice(1)
    .filter((lane) => lane.members.length > 0).length;

  const input: Layout2DInput = {
    nodeGroupIds,
    nodeKindRanks,
    nodeWidths,
    nodeHeights,
    edgeSrcs: new Uint32Array(edgeSrcList),
    edgeDsts: new Uint32Array(edgeDstList),
    edgeWeights: new Float32Array(edgeWeightList),
    groupCount: g,
    groupMaxCols,
    groupBaryFlags,
    params: {
      lanePadX: isCompact ? 18 : 22,
      lanePadTop: 74,
      lanePadBottom: 22,
      cardGapX: isCompact ? 16 : 22,
      cardGapY: isCompact ? 14 : 24,
      laneGapX: isCompact ? 38 : 48,
      laneGapY: isCompact ? 34 : 40,
      maxLanesPerRow: nonEmptyContentLanes <= 4 ? 2 : nonEmptyContentLanes <= 9 ? 3 : 4,
    },
  };

  return { lanes, flatItems, input };
}

function materializeFlowNodes(
  model: Prepared2DModel,
  layout: Layout2DResult,
  isCompact: boolean,
): AnyFlowNode[] {
  const originX = 36;
  const originY = 36;
  const groupNodes: AnyFlowNode[] = [];
  const cardNodes: AnyFlowNode[] = [];

  model.lanes.forEach((lane, g) => {
    if (lane.members.length === 0) return;
    const gx = layout.groupRects[g * 4] + originX;
    const gy = layout.groupRects[g * 4 + 1] + originY;
    const gw = layout.groupRects[g * 4 + 2];
    const gh = layout.groupRects[g * 4 + 3];

    groupNodes.push({
      id: `swimlane-${lane.key}`,
      type: 'sectionGroup',
      position: { x: gx, y: gy },
      style: { width: gw, height: gh },
      data: {
        title: lane.title,
        pill: lane.pill,
        unitLabel: lane.unitLabel,
        tone: lane.tone,
        count: lane.members.length,
        sectionId: lane.sectionId,
      },
      draggable: false,
      selectable: false,
      focusable: false,
      zIndex: -1,
    } as AnyFlowNode);
  });

  model.flatItems.forEach((item, idx) => {
    const x = layout.nodeXY[idx * 2] + originX;
    const y = layout.nodeXY[idx * 2 + 1] + originY;
    const effectiveCompact = isCompact || item.forceCompact;

    cardNodes.push({
      id: item.node.id,
      type: 'evidence',
      position: { x, y },
      data: {
        ...item.node,
        isCompact: effectiveCompact,
        symbols: item.symbols,
        sectionId: item.sectionId,
      },
      draggable: false,
      selectable: true,
      focusable: true,
      ariaRole: 'button',
      ariaLabel: `${item.node.kind}: ${item.node.label}. ${item.node.expression}`,
    });
  });

  return [...groupNodes, ...cardNodes];
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

function SectionGroupCard({ data }: NodeProps<SectionGroupFlowNode>) {
  return (
    <div
      className={`section-swimlane-card is-tone-${data.tone}`}
      aria-hidden="true"
    >
      <div className="section-swimlane-header">
        <div className="section-swimlane-meta-row">
          <span className={`section-swimlane-pill is-tone-${data.tone}`}>
            {data.pill}
          </span>
          {data.count > 0 ? (
            <span className="section-swimlane-badge">
              {data.count} {data.unitLabel}
            </span>
          ) : null}
        </div>
        <strong className="section-swimlane-title" title={data.title}>
          {data.title}
        </strong>
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
    if (nodeCount === 0) return;

    const timer = setTimeout(() => {
      const stage =
        typeof document !== 'undefined'
          ? document.querySelector('.react-flow-stage')
          : null;
      if (stage && (stage.clientWidth === 0 || stage.clientHeight === 0)) return;

      void fitView({
        padding: 0.12,
        maxZoom: 1.0,
        minZoom: 0.22,
        duration: hasFittedRef.current ? 240 : 0,
      });
      hasFittedRef.current = true;
    }, 120);

    return () => clearTimeout(timer);
  }, [nodesInitialized, nodeCount, filterKey, fitView]);

  useEffect(() => {
    let resizeTimer: ReturnType<typeof setTimeout>;
    const handleResize = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        const stage =
          typeof document !== 'undefined'
            ? document.querySelector('.react-flow-stage')
            : null;
        if (stage && (stage.clientWidth === 0 || stage.clientHeight === 0)) return;

        void fitView({
          padding: 0.14,
          maxZoom: 1.0,
          minZoom: 0.22,
          duration: 240,
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
  const [showIntraSectionDerivations, setShowIntraSectionDerivations] = useState(true);
  const [showCrossSectionDerivations, setShowCrossSectionDerivations] = useState(true);
  const activeFilterCount =
    disabledKinds.size +
    disabledRelations.size +
    (!showIntraSectionDerivations ? 1 : 0) +
    (!showCrossSectionDerivations ? 1 : 0);

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

  // Unified 2D model + instant synchronous first-paint layout + async Rust/WASM Worker execution.
  const preparedModel = useMemo(
    () => prepareUnified2DModel(visibleNodes, edges, isCompactMode),
    [visibleNodes, edges, isCompactMode],
  );

  const syncLayout = useMemo(
    () => computeLayout2DFallback(preparedModel.input),
    [preparedModel],
  );

  const [wasmLayoutState, setWasmLayoutState] = useState<{
    model: Prepared2DModel;
    result: Layout2DResult;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    void computeLayout2DAsync(preparedModel.input).then((result) => {
      if (!cancelled) {
        setWasmLayoutState({ model: preparedModel, result });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [preparedModel]);

  const activeLayout =
    wasmLayoutState?.model === preparedModel ? wasmLayoutState.result : syncLayout;
  const layoutEngine: LayoutEngineKind = activeLayout.engine;

  const baseLayoutNodes = useMemo(
    () => materializeFlowNodes(preparedModel, activeLayout, isCompactMode),
    [preparedModel, activeLayout, isCompactMode],
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

    // 1. Authoritative real graph edges (semantic relations from Neo4j / snapshot).
    for (const edge of visibleEdges) {
      const srcPos = nodePosMap.get(edge.source);
      const tgtPos = nodePosMap.get(edge.target);
      if (!srcPos || !tgtPos) continue;

      const isContainment =
        edge.relation === 'contains' || edge.relation === 'has_version';
      // Structural containment is visually encoded by the frosted lane containers.
      if (isContainment) continue;

      const isHighlighted = activeEdgeIds !== null && activeEdgeIds.has(edge.id);
      const isDimmed = activeEdgeIds !== null && !isHighlighted;
      connectedPairs.add(`${edge.source}->${edge.target}`);

      const stroke = edgeColors[edge.relation] ?? '#64748b';
      const dx = tgtPos.x - srcPos.x;
      const dy = tgtPos.y - srcPos.y;
      let sourceHandle: string;
      let targetHandle: string;
      let edgeType: string;

      if (edge.relation === 'defines' || nodeKindMap.get(edge.target) === 'Symbol') {
        sourceHandle = 'right';
        targetHandle = 'left';
        edgeType = 'smoothstep';
      } else if (Math.abs(dx) < 80) {
        sourceHandle = dy >= 0 ? 'bottom' : 'top';
        targetHandle = dy >= 0 ? 'top' : 'bottom';
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

      resultEdges.push({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle,
        targetHandle,
        type: edgeType,
        label: isHighlighted ? relationLabel(edge.relation) : undefined,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: stroke,
          width: 14,
          height: 14,
        },
        selectable: true,
        focusable: true,
        animated: edge.relation === 'supersedes' || isHighlighted,
        zIndex: isHighlighted ? 20 : isDimmed ? 1 : 12,
        style: {
          stroke,
          strokeWidth: isHighlighted ? 2.5 : isDimmed ? 1.15 : 1.9,
          opacity: isDimmed ? 0.25 : 0.94,
          transition: 'opacity 0.18s ease, stroke-width 0.18s ease',
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
          strokeWidth: 1.4,
        },
        labelBgPadding: [3, 7] as [number, number],
        labelBgBorderRadius: 6,
        ariaLabel: `${relationLabel(edge.relation)} relation`,
      });
    }

    // 2. Subtle dashed reading-order guides (visually distinct from real mathematical derivations!).
    const secEqMap = new Map<string, string[]>();
    for (const fn of baseLayoutNodes) {
      if (fn.type === 'sectionGroup') {
        const secId = (fn.data as SectionGroupData).sectionId;
        if (secId) secEqMap.set(secId, []);
      }
    }
    for (const fn of baseLayoutNodes) {
      if (
        fn.type === 'evidence' &&
        (fn.data as EvidenceNodeData).kind === 'Equation'
      ) {
        const secId = (fn.data as EvidenceNodeData).sectionId;
        if (secId && secEqMap.has(secId)) {
          secEqMap.get(secId)!.push(fn.id);
        }
      }
    }

    const activeSectionIds = [...secEqMap.keys()].filter(
      (sid) => (secEqMap.get(sid)?.length ?? 0) > 0,
    );
    const guideStroke = '#94a3b8';

    if (showIntraSectionDerivations) {
      activeSectionIds.forEach((secId) => {
        const eqIds = secEqMap.get(secId) ?? [];
        for (let i = 0; i < eqIds.length - 1; i++) {
          const srcId = eqIds[i];
          const tgtId = eqIds[i + 1];
          const pairKey = `${srcId}->${tgtId}`;
          const revKey = `${tgtId}->${srcId}`;
          if (connectedPairs.has(pairKey) || connectedPairs.has(revKey)) continue;

          const srcPos = nodePosMap.get(srcId);
          const tgtPos = nodePosMap.get(tgtId);
          // Skip vertical connector across folded sub-columns inside the same lane.
          if (srcPos && tgtPos && Math.abs(tgtPos.x - srcPos.x) > 80) continue;

          connectedPairs.add(pairKey);
          const isHighlighted =
            activeFocusId !== null &&
            (activeFocusId === srcId || activeFocusId === tgtId);
          const isDimmed = activeFocusId !== null && !isHighlighted;

          resultEdges.push({
            id: `reading-seq-${srcId}-${tgtId}`,
            source: srcId,
            target: tgtId,
            sourceHandle: 'bottom',
            targetHandle: 'top',
            type: 'smoothstep',
            markerEnd: {
              type: MarkerType.Arrow,
              color: isHighlighted ? '#475569' : guideStroke,
              width: 13,
              height: 13,
            },
            selectable: false,
            focusable: false,
            zIndex: isHighlighted ? 14 : 2,
            style: {
              stroke: isHighlighted ? '#475569' : guideStroke,
              strokeWidth: isHighlighted ? 1.8 : 1.35,
              strokeDasharray: '4 4',
              opacity: isDimmed ? 0.12 : isHighlighted ? 0.85 : 0.45,
              transition: 'opacity 0.18s ease',
            },
            ariaLabel: 'document reading sequence',
          });
        }
      });
    }

    if (showCrossSectionDerivations) {
      for (let s = 0; s < activeSectionIds.length - 1; s++) {
        const currEqs = secEqMap.get(activeSectionIds[s]) ?? [];
        const nextEqs = secEqMap.get(activeSectionIds[s + 1]) ?? [];
        if (currEqs.length === 0 || nextEqs.length === 0) continue;

        const lastEqId = currEqs[currEqs.length - 1];
        const firstNextEqId = nextEqs[0];
        const bridgeKey = `${lastEqId}->${firstNextEqId}`;
        if (connectedPairs.has(bridgeKey)) continue;

        const lastPos = nodePosMap.get(lastEqId);
        const nextPos = nodePosMap.get(firstNextEqId);
        // Never slash diagonally backward across row bands.
        if (!lastPos || !nextPos || nextPos.x <= lastPos.x) continue;

        connectedPairs.add(bridgeKey);
        const isHighlighted =
          activeFocusId !== null &&
          (activeFocusId === lastEqId || activeFocusId === firstNextEqId);
        const isDimmed = activeFocusId !== null && !isHighlighted;

        resultEdges.push({
          id: `reading-bridge-${lastEqId}-${firstNextEqId}`,
          source: lastEqId,
          target: firstNextEqId,
          sourceHandle: 'right',
          targetHandle: 'left',
          type: 'default',
          markerEnd: {
            type: MarkerType.Arrow,
            color: isHighlighted ? '#0f766e' : '#64748b',
            width: 13,
            height: 13,
          },
          selectable: false,
          focusable: false,
          zIndex: isHighlighted ? 14 : 2,
          style: {
            stroke: isHighlighted ? '#0f766e' : '#64748b',
            strokeWidth: isHighlighted ? 1.8 : 1.3,
            strokeDasharray: '6 5',
            opacity: isDimmed ? 0.1 : isHighlighted ? 0.85 : 0.36,
            transition: 'opacity 0.18s ease',
          },
          ariaLabel: 'section reading transition',
        });
      }
    }

    return resultEdges;
  }, [
    baseLayoutNodes,
    visibleEdges,
    activeEdgeIds,
    activeFocusId,
    showIntraSectionDerivations,
    showCrossSectionDerivations,
  ]);

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
      }, 45);
    },
    [],
  );
  const handleNodeMouseLeave = useCallback(() => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    hoverTimerRef.current = setTimeout(() => {
      setHoveredNodeId(null);
    }, 45);
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
        <span>
          {hasSnapshot
            ? 'This paper has no extracted equations. Its source and lineage records remain available.'
            : 'Import an arXiv HTML paper to create the first evidence snapshot.'}
        </span>
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
            onMouseEnter={prefetch3DViewport}
            onFocus={prefetch3DViewport}
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
            <fieldset>
              <legend>Reading Guides</legend>
              <div className="graph-filter-options">
                <label htmlFor="filter-flow-intra">
                  <Checkbox
                    id="filter-flow-intra"
                    name="filter_flow_intra"
                    checked={showIntraSectionDerivations}
                    onCheckedChange={(checked) =>
                      setShowIntraSectionDerivations(checked === true)
                    }
                  />
                  Intra-section sequence
                </label>
                <label htmlFor="filter-flow-cross">
                  <Checkbox
                    id="filter-flow-cross"
                    name="filter_flow_cross"
                    checked={showCrossSectionDerivations}
                    onCheckedChange={(checked) =>
                      setShowCrossSectionDerivations(checked === true)
                    }
                  />
                  Cross-section reading guides
                </label>
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
                  setShowIntraSectionDerivations(true);
                  setShowCrossSectionDerivations(true);
                }}
                disabled={activeFilterCount === 0}
              >
                <RotateCcw size={13} aria-hidden="true" />
                Reset
              </Button>
            </div>
          </div>
        </details>
        {viewMode === '2d' ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={`density-toggle-btn ${isCompactMode ? 'is-active' : ''}`}
            onClick={() => setIsCompactMode((prev) => !prev)}
            title={
              isCompactMode
                ? 'Switch to Detailed Formula Cards'
                : 'Switch to Compact Nodes (Fits large graphs in view)'
            }
            aria-label={isCompactMode ? 'Detailed Cards' : 'Compact Nodes'}
          >
            <LayoutGrid size={13} className="mr-1 text-indigo-500" />
            <span>{isCompactMode ? 'Compact' : 'Detailed'}</span>
          </Button>
        ) : null}
        {onToggleZen ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={`zen-toggle-btn ${isZenMode ? 'is-active' : ''}`}
            onClick={onToggleZen}
            title={
              isZenMode
                ? 'Exit Zen Mode (Press Z)'
                : 'Zen Focus Mode: Hide Sidebars (Press Z)'
            }
            aria-label={isZenMode ? 'Exit Zen Mode' : 'Zen Focus Mode'}
          >
            <Eye size={13} className="mr-1 text-sky-500" />
            <span>{isZenMode ? 'Exit Zen' : 'Zen'}</span>
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
          <span>{isFullscreen ? 'Exit' : 'Expand'}</span>
        </Button>
      </div>

      <section
        className={`react-flow-stage ${viewMode === '3d' ? 'is-spatial' : ''}`}
        aria-label="Interactive evidence graph"
      >
        {viewMode === '3d' ? (
          <Suspense
            fallback={
              <div className="flex items-center justify-center w-full h-full min-h-0 text-slate-300 bg-[#060b16] text-xs font-mono tracking-wide">
                Loading 3D Scientific Observatory…
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
              minZoom={0.22}
              maxZoom={2}
              fitView
              fitViewOptions={{ padding: 0.12, maxZoom: 1.0 }}
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
                filterKey={`${disabledKinds.size}-${disabledRelations.size}-${flowNodes.length}-${isCompactMode ? 'c' : 'd'}-${isZenMode ? 'z' : 'n'}-${layoutEngine}`}
              />
              <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
              <Panel position="bottom-right" className="minimap-panel">
                <div className="minimap-panel-actions">
                  {layoutEngine === 'rust-wasm' ? (
                    <span
                      className="layout-engine-badge"
                      title={`Rust WebAssembly Worker layout (${activeLayout.durationMs.toFixed(1)} ms)`}
                    >
                      <Cpu size={11} aria-hidden="true" />
                      WASM
                    </span>
                  ) : null}
                  <button
                    type="button"
                    className="minimap-toggle-btn"
                    onClick={() => setIsMinimapOpen((prev) => !prev)}
                    aria-expanded={isMinimapOpen}
                    title={
                      isMinimapOpen
                        ? 'Collapse Overview MiniMap'
                        : 'Expand Overview MiniMap'
                    }
                  >
                    <Compass size={13} className="shrink-0 text-cyan-500" />
                    <span>Overview</span>
                    <span className="minimap-toggle-arrow">
                      {isMinimapOpen ? '▼' : '▲'}
                    </span>
                  </button>
                </div>
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
                        if (node.type === 'sectionGroup') return 'rgba(148, 163, 184, 0.25)';
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

      {selectedId ? (
        <a className="mobile-inspector-link" href="#formula-inspector">
          View selected evidence
        </a>
      ) : null}
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
