'use client';

import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type OnSelectionChangeParams,
} from '@xyflow/react';
import { RotateCcw } from 'lucide-react';
import { memo, useCallback, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import type {
  EvidenceEntityType,
  EvidenceRelationType,
} from '@/lib/import-types';

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

function EvidenceNodeCard({ data, selected }: NodeProps<EvidenceFlowNode>) {
  return (
    <article
      className={`flow-evidence-node ${kindTone[data.kind]} ${selected ? 'is-selected' : ''}`}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <span>{data.kind}</span>
      <strong>{data.label}</strong>
      <code>{data.expression}</code>
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
): EvidenceFlowNode[] {
  const rows = new Map<number, number>();
  return [...nodes]
    .sort((left, right) => {
      const column = kindColumns[left.kind] - kindColumns[right.kind];
      return (
        column ||
        left.label.localeCompare(right.label) ||
        left.id.localeCompare(right.id)
      );
    })
    .map((node) => {
      const column = kindColumns[node.kind];
      const row = rows.get(column) ?? 0;
      rows.set(column, row + 1);
      return {
        id: node.id,
        type: 'evidence',
        position: { x: 36 + column * 292, y: 36 + row * 220 },
        data: node,
        draggable: false,
        selectable: true,
        focusable: true,
        ariaRole: 'button',
        ariaLabel: `${node.kind}: ${node.label}. ${node.expression}`,
      };
    });
}

export default function EvidenceGraphViewport({
  nodes,
  edges,
  selectedId,
  onSelect,
  loading = false,
}: EvidenceGraphViewportProps) {
  const relations = useMemo(
    () => [...new Set(edges.map((edge) => edge.relation))].sort(),
    [edges],
  );
  const [disabledRelations, setDisabledRelations] = useState<
    Set<EvidenceRelationType>
  >(() => new Set());

  const flowNodes = useMemo(
    () => layoutNodes(nodes),
    [nodes],
  );
  const flowEdges = useMemo<Edge[]>(
    () =>
      edges
        .filter((edge) => !disabledRelations.has(edge.relation))
        .map((edge) => ({
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: relationLabel(edge.relation),
          type: 'smoothstep',
          selectable: true,
          focusable: true,
          animated: edge.relation === 'supersedes',
          style: { stroke: edgeColors[edge.relation], strokeWidth: 1.8 },
          labelStyle: { fill: '#4b5870', fontSize: 11, fontWeight: 650 },
          labelBgStyle: { fill: '#f8faff', fillOpacity: 0.94 },
          ariaLabel: `${relationLabel(edge.relation)} relation`,
        })),
    [disabledRelations, edges],
  );

  const handleNodeClick = useCallback(
    (_event: React.MouseEvent, node: EvidenceFlowNode) => onSelect(node.id),
    [onSelect],
  );
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
    <div className="persisted-graph-shell">
      <div className="relation-toolbar" aria-label="Relation filters">
        <span>Relations</span>
        <div>
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
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setDisabledRelations(new Set())}
          disabled={disabledRelations.size === 0}
        >
          <RotateCcw size={13} />
          Reset
        </Button>
        <output className="visible-edge-count" aria-live="polite">
          {flowEdges.length} of {edges.length} relations
        </output>
      </div>

      <section
        className="react-flow-stage"
        aria-label="Interactive evidence graph"
      >
        <span className="sr-only" id="graph-keyboard-help">
          Use arrow keys to move selection between evidence nodes. Press Enter or
          Space on a focused node to select it.
        </span>
        <ReactFlow<EvidenceFlowNode, Edge>
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          onNodeClick={handleNodeClick}
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
          fitViewOptions={{ padding: 0.18, maxZoom: 1.15 }}
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
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
          <MiniMap
            pannable
            zoomable
            ariaLabel="Evidence graph overview"
            nodeColor={(node) => {
              const kind = (node.data as EvidenceNodeData).kind;
              if (kind === 'Hypothesis') return '#b14d76';
              if (kind === 'Equation') return '#5366d9';
              if (kind === 'Section') return '#0f9d8a';
              return '#7b879f';
            }}
          />
          <Controls showInteractive={false} />
        </ReactFlow>
      </section>

      <ol className="mobile-graph-list" aria-label="Evidence nodes">
        {nodes.map((node) => (
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
