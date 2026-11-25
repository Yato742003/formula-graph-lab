'use client';

import * as THREE from 'three';
import {
  Cpu,
  Focus,
  Pause,
  Play,
  RotateCcw,
  Sparkles,
  TreePine,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Slider } from '@/components/ui/slider';
import {
  renderFormulaHtml,
  type GraphViewportEdge,
  type GraphViewportNode,
} from '@/lib/graph-formula-render';
import {
  computeLayout3DAsync,
  computeLayout3DFallback,
  type Layout3DInput,
  type LayoutEngineKind,
} from '@/lib/graph-layout-wasm';
import type { EvidenceEntityType, EvidenceRelationType } from '@/lib/import-types';

type Graph3DViewportProps = {
  nodes: GraphViewportNode[];
  edges: GraphViewportEdge[];
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
  isFullscreen?: boolean;
};

type CameraPreset = 'overview' | 'roots' | 'canopy';
type MeshFamily = 'sphere' | 'octahedron' | 'tetrahedron' | 'dodecahedron';

// Scientific Observatory semantic palette aligned with DESIGN.md tokens on #060b16 slate.
const kindColors: Record<EvidenceEntityType, number> = {
  Paper: 0xf8fafc,        // Crisp observatory white
  PaperVersion: 0x38bdf8, // Sky cyan
  Section: 0x2dd4bf,      // Structural teal
  Equation: 0x60a5fa,     // Evidence cobalt blue
  Symbol: 0xa78bfa,       // Soft violet satellite
  Assumption: 0xfb7185,   // Rose boundary
  Claim: 0xe879f9,        // Review orchid
  Concept: 0x34d399,      // Citation emerald
  Method: 0x38bdf8,       // Method sky
  Experiment: 0x22d3ee,   // Experiment cyan
  Hypothesis: 0xf59e0b,   // Derivation / hypothesis amber
};

const kindRadius: Record<EvidenceEntityType, number> = {
  Paper: 1.72,
  PaperVersion: 1.38,
  Section: 0.78,
  Equation: 1.24,
  Symbol: 0.48,
  Assumption: 0.74,
  Claim: 0.86,
  Concept: 0.82,
  Method: 0.84,
  Experiment: 0.82,
  Hypothesis: 1.26,
};

const edgeHexColors: Record<EvidenceRelationType, number> = {
  has_version: 0x38bdf8,
  contains: 0x334155,
  defines: 0x2dd4bf,
  uses: 0x38bdf8,
  assumes: 0xf59e0b,
  cites: 0x64748b,
  makes_claim: 0xc084fc,
  about: 0xa78bfa,
  derived_from: 0x60a5fa,
  approximates: 0xf59e0b,
  generalizes: 0x14b8a6,
  equivalent_under: 0x22d3ee,
  disagrees_with: 0xf87171,
  supersedes: 0xf472b6,
};

function kindToLayoutCode(kind: EvidenceEntityType): number {
  if (kind === 'Paper' || kind === 'PaperVersion') return 0;
  if (kind === 'Section') return 1;
  if (kind === 'Equation') return 2;
  if (kind === 'Symbol') return 3;
  if (kind === 'Claim' || kind === 'Hypothesis') return 5;
  return 4;
}

function relationToLayoutCode(rel: EvidenceRelationType): number {
  if (rel === 'contains' || rel === 'has_version') return 0;
  if (rel === 'derived_from' || rel === 'generalizes' || rel === 'approximates') return 1;
  if (rel === 'defines') return 2;
  if (rel === 'uses') return 3;
  if (rel === 'cites' || rel === 'disagrees_with' || rel === 'supersedes') return 4;
  return 5;
}

function kindToMeshFamily(kind: EvidenceEntityType): MeshFamily {
  if (kind === 'Equation') return 'octahedron';
  if (kind === 'Symbol') return 'tetrahedron';
  if (kind === 'Hypothesis' || kind === 'Claim' || kind === 'Assumption') {
    return 'dodecahedron';
  }
  return 'sphere';
}

function buildLayout3DInput(
  nodes: GraphViewportNode[],
  edges: GraphViewportEdge[],
): Layout3DInput {
  const n = nodes.length;
  const indexById = new Map<string, number>();
  nodes.forEach((node, idx) => indexById.set(node.id, idx));

  const nodeKinds = new Uint32Array(n);
  const nodeParents = new Int32Array(n).fill(-1);
  const nodeRootPapers = new Int32Array(n).fill(0);

  const paperSlots = new Map<number, number>();
  let slotCounter = 0;
  for (let i = 0; i < n; i++) {
    const code = kindToLayoutCode(nodes[i].kind);
    nodeKinds[i] = code;
    if (code === 0) {
      paperSlots.set(i, slotCounter++);
    }
  }

  // Build hierarchical parent links: Section->Paper, Equation->Section, Symbol->Equation.
  const parentEdgePriority = (rel: EvidenceRelationType): number => {
    if (rel === 'contains') return 3;
    if (rel === 'defines') return 3;
    if (rel === 'has_version') return 2;
    if (rel === 'uses' || rel === 'makes_claim' || rel === 'about') return 1;
    return 0;
  };
  const bestPriority = new Int8Array(n);

  const edgeSrcList: number[] = [];
  const edgeDstList: number[] = [];
  const edgeCodeList: number[] = [];

  for (const edge of edges) {
    const u = indexById.get(edge.source);
    const v = indexById.get(edge.target);
    if (u === undefined || v === undefined || u === v) continue;
    edgeSrcList.push(u);
    edgeDstList.push(v);
    edgeCodeList.push(relationToLayoutCode(edge.relation));

    const prio = parentEdgePriority(edge.relation);
    if (prio > bestPriority[v]) {
      bestPriority[v] = prio;
      nodeParents[v] = u;
    }
  }

  // Fallback section matching via `#S\d+` anchor metadata if no explicit `contains` edge exists.
  for (let i = 0; i < n; i++) {
    if (nodeKinds[i] === 2 && nodeParents[i] === -1 && nodes[i].meta) {
      const m = nodes[i].meta.match(/#?(S\d+)/i);
      if (m) {
        const secIdx = nodes.findIndex(
          (candidate) =>
            candidate.kind === 'Section' &&
            (candidate.id.includes(m[1]) || candidate.label.includes(m[1])),
        );
        if (secIdx >= 0) nodeParents[i] = secIdx;
      }
    }
  }

  // Resolve owning paper slot for every node.
  for (let i = 0; i < n; i++) {
    let cur = i;
    let depth = 0;
    let slot = 0;
    while (cur >= 0 && depth < 8) {
      const found = paperSlots.get(cur);
      if (found !== undefined) {
        slot = found;
        break;
      }
      cur = nodeParents[cur];
      depth++;
    }
    nodeRootPapers[i] = slot;
  }

  return {
    nodeKinds,
    nodeParents,
    nodeRootPapers,
    edgeSrcs: new Uint32Array(edgeSrcList),
    edgeDsts: new Uint32Array(edgeDstList),
    edgeKindCodes: new Uint32Array(edgeCodeList),
  };
}

type OrbitState = {
  target: THREE.Vector3;
  desiredTarget: THREE.Vector3;
  theta: number;
  desiredTheta: number;
  phi: number;
  desiredPhi: number;
  radius: number;
  desiredRadius: number;
};

type EdgeSegmentMeta = {
  sourceIdx: number;
  targetIdx: number;
  sourceId: string;
  targetId: string;
  relation: EvidenceRelationType;
  baseColor: THREE.Color;
  baseIntensity: number;
};

const CURVE_SEGMENTS = 12;

export default function Graph3DViewport({
  nodes,
  edges,
  selectedId,
  onSelect,
  isFullscreen = false,
}: Graph3DViewportProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const labelsLayerRef = useRef<HTMLDivElement>(null);
  const [hoveredNode, setHoveredNode] = useState<GraphViewportNode | null>(null);
  const [autoRotate, setAutoRotate] = useState(true);
  const [timelineProgress, setTimelineProgress] = useState(100);
  const [focusActive, setFocusActive] = useState(false);
  const [layoutEngine, setLayoutEngine] = useState<LayoutEngineKind>('ts-fallback');

  const orbitRef = useRef<OrbitState>({
    target: new THREE.Vector3(0, 0, 0),
    desiredTarget: new THREE.Vector3(0, 0, 0),
    theta: 0.62,
    desiredTheta: 0.62,
    phi: 1.18,
    desiredPhi: 1.18,
    radius: 56,
    desiredRadius: 56,
  });

  const framingPresetsRef = useRef<{
    overview: { target: THREE.Vector3; theta: number; phi: number; radius: number };
    roots: { target: THREE.Vector3; theta: number; phi: number; radius: number };
    canopy: { target: THREE.Vector3; theta: number; phi: number; radius: number };
  }>({
    overview: { target: new THREE.Vector3(0, 0, 0), theta: 0.62, phi: 1.18, radius: 56 },
    roots: { target: new THREE.Vector3(-12, 0, 0), theta: -0.35, phi: 1.24, radius: 34 },
    canopy: { target: new THREE.Vector3(10, 4, 0), theta: 1.15, phi: 0.92, radius: 38 },
  });

  // Compute lineage neighborhood for branch isolation.
  const { forwardAdj, backwardAdj } = useMemo(() => {
    const forward = new Map<string, Set<string>>();
    const backward = new Map<string, Set<string>>();
    for (const edge of edges) {
      if (!forward.has(edge.source)) forward.set(edge.source, new Set());
      forward.get(edge.source)!.add(edge.target);
      if (!backward.has(edge.target)) backward.set(edge.target, new Set());
      backward.get(edge.target)!.add(edge.source);
    }
    return { forwardAdj: forward, backwardAdj: backward };
  }, [edges]);

  const focusedLineage = useMemo<Set<string> | null>(() => {
    if (!selectedId || !focusActive) return null;
    const result = new Set<string>([selectedId]);
    const queueUp = [selectedId];
    while (queueUp.length > 0) {
      const cur = queueUp.shift()!;
      for (const p of backwardAdj.get(cur) ?? []) {
        if (!result.has(p)) {
          result.add(p);
          queueUp.push(p);
        }
      }
    }
    const queueDown = [selectedId];
    while (queueDown.length > 0) {
      const cur = queueDown.shift()!;
      for (const c of forwardAdj.get(cur) ?? []) {
        if (!result.has(c)) {
          result.add(c);
          queueDown.push(c);
        }
      }
    }
    return result;
  }, [selectedId, focusActive, forwardAdj, backwardAdj]);

  // Refs synced into the live WebGL animation & state updater without rebuilding the renderer.
  const onSelectRef = useRef(onSelect);
  const autoRotateRef = useRef(autoRotate);
  const reducedMotionRef = useRef(false);
  const selectedIdRef = useRef<string | null>(selectedId);
  const hoveredIdRef = useRef<string | null>(null);
  const timelineRef = useRef<number>(timelineProgress);
  const focusedLineageRef = useRef<Set<string> | null>(focusedLineage);
  const syncVisualsRef = useRef<() => void>(() => {});

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);
  useEffect(() => {
    autoRotateRef.current = autoRotate;
  }, [autoRotate]);
  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);
  useEffect(() => {
    timelineRef.current = timelineProgress;
  }, [timelineProgress]);
  useEffect(() => {
    focusedLineageRef.current = focusedLineage;
  }, [focusedLineage]);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const syncPreference = () => {
      reducedMotionRef.current = preference.matches;
      if (preference.matches) setAutoRotate(false);
    };
    syncPreference();
    preference.addEventListener('change', syncPreference);
    return () => preference.removeEventListener('change', syncPreference);
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const layoutInput = buildLayout3DInput(nodes, edges);
    const initialLayout = computeLayout3DFallback(layoutInput);
    const nodePositions: THREE.Vector3[] = [];
    const nodeProgress: number[] = [];

    for (let i = 0; i < nodes.length; i++) {
      nodePositions.push(
        new THREE.Vector3(
          initialLayout.nodeXYZP[i * 4] ?? 0,
          initialLayout.nodeXYZP[i * 4 + 1] ?? 0,
          initialLayout.nodeXYZP[i * 4 + 2] ?? 0,
        ),
      );
      nodeProgress.push(initialLayout.nodeXYZP[i * 4 + 3] ?? 0.5);
    }

    const indexById = new Map<string, number>();
    nodes.forEach((node, idx) => indexById.set(node.id, idx));

    // Track sections that own at least one equation so empty text subsections stay visually quiet.
    const equationSectionIds = new Set<string>();
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].kind === 'Equation') {
        const pIdx = layoutInput.nodeParents[i];
        if (pIdx >= 0 && pIdx < nodes.length && nodes[pIdx].kind === 'Section') {
          equationSectionIds.add(nodes[pIdx].id);
        }
      }
    }
    const effectiveRadius = (node: GraphViewportNode): number => {
      if (node.kind === 'Section' && equationSectionIds.size > 0 && !equationSectionIds.has(node.id)) {
        return 0.36;
      }
      return kindRadius[node.kind] ?? 0.85;
    };

    // Scene & lighting: calm deep observatory slate (#060b16), crisp hemisphere + key/rim lighting.
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x060b16);
    scene.fog = new THREE.FogExp2(0x060b16, 0.0042);

    const width = container.clientWidth || 800;
    const height = container.clientHeight || 420;
    const camera = new THREE.PerspectiveCamera(44, width / height, 0.5, 1200);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    } catch {
      const fallback = document.createElement('output');
      fallback.className = 'spatial-webgl-fallback';
      const heading = document.createElement('strong');
      heading.textContent = '3D WebGL context unavailable';
      const note = document.createElement('span');
      note.textContent =
        'Switch back to 2D Map or enable hardware acceleration to explore the 3D observatory.';
      fallback.appendChild(heading);
      fallback.appendChild(note);
      container.replaceChildren(fallback);
      return;
    }
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    container.replaceChildren(renderer.domElement);

    const hemiLight = new THREE.HemisphereLight(0xdbeafe, 0x0f172a, 0.95);
    scene.add(hemiLight);

    const keyLight = new THREE.DirectionalLight(0xf8fafc, 1.25);
    keyLight.position.set(40, 65, 50);
    scene.add(keyLight);

    const rimLight = new THREE.DirectionalLight(0x38bdf8, 0.65);
    rimLight.position.set(-45, -25, -40);
    scene.add(rimLight);

    // 1. Subtle Observatory Coordinate Rings & Spine Reference (Y = 0 plane)
    const refGroup = new THREE.Group();
    scene.add(refGroup);
    const ringRadii = [12, 22, 32];
    for (const r of ringRadii) {
      const segs = 96;
      const pts: THREE.Vector3[] = [];
      for (let s = 0; s <= segs; s++) {
        const a = (s / segs) * Math.PI * 2;
        pts.push(new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r));
      }
      const rGeom = new THREE.BufferGeometry().setFromPoints(pts);
      const rMat = new THREE.LineBasicMaterial({
        color: 0x334155,
        transparent: true,
        opacity: r === 12 ? 0.24 : 0.14,
      });
      refGroup.add(new THREE.Line(rGeom, rMat));
    }

    // Subtle cross-axis tick lines for spatial orientation.
    const axisGeom = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-36, 0, 0),
      new THREE.Vector3(36, 0, 0),
      new THREE.Vector3(0, 0, -36),
      new THREE.Vector3(0, 0, 36),
    ]);
    const axisMat = new THREE.LineBasicMaterial({
      color: 0x1e293b,
      transparent: true,
      opacity: 0.45,
    });
    refGroup.add(new THREE.LineSegments(axisGeom, axisMat));

    // Restrained distant ambient dust (200 muted points — never distracts from nodes).
    const dustCount = 200;
    const dustPos = new Float32Array(dustCount * 3);
    for (let i = 0; i < dustCount; i++) {
      // Deterministic spherical Fibonacci shell outside graph bounds.
      const y = 1 - (i / (dustCount - 1)) * 2;
      const r = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = i * 2.3999632;
      const dist = 110 + ((i * 29) % 70);
      dustPos[i * 3] = Math.cos(theta) * r * dist;
      dustPos[i * 3 + 1] = y * dist * 0.72;
      dustPos[i * 3 + 2] = Math.sin(theta) * r * dist;
    }
    const dustGeom = new THREE.BufferGeometry();
    dustGeom.setAttribute('position', new THREE.BufferAttribute(dustPos, 3));
    const dustMat = new THREE.PointsMaterial({
      color: 0x94a3b8,
      size: 0.55,
      transparent: true,
      opacity: 0.26,
    });
    scene.add(new THREE.Points(dustGeom, dustMat));

    // 2. GPU InstancedMesh Pools (at most 4 draw calls for all nodes!)
    const familyMembers: Record<MeshFamily, number[]> = {
      sphere: [],
      octahedron: [],
      tetrahedron: [],
      dodecahedron: [],
    };
    const nodeFamilySlot: Array<{ family: MeshFamily; slot: number }> = [];

    nodes.forEach((node, idx) => {
      const fam = kindToMeshFamily(node.kind);
      const slot = familyMembers[fam].length;
      familyMembers[fam].push(idx);
      nodeFamilySlot.push({ family: fam, slot });
    });

    const familyGeometries: Record<MeshFamily, THREE.BufferGeometry> = {
      sphere: new THREE.IcosahedronGeometry(1, 3),
      octahedron: new THREE.OctahedronGeometry(1, 0),
      tetrahedron: new THREE.TetrahedronGeometry(1, 0),
      dodecahedron: new THREE.DodecahedronGeometry(1, 0),
    };

    const instancedMeshes: Partial<Record<MeshFamily, THREE.InstancedMesh>> = {};
    const raycastTargets: THREE.InstancedMesh[] = [];

    (Object.keys(familyMembers) as MeshFamily[]).forEach((fam) => {
      const members = familyMembers[fam];
      if (members.length === 0) return;
      const mat = new THREE.MeshStandardMaterial({
        color: 0xffffff,
        roughness: fam === 'octahedron' ? 0.22 : 0.32,
        metalness: fam === 'octahedron' ? 0.28 : 0.16,
      });
      const mesh = new THREE.InstancedMesh(familyGeometries[fam], mat, members.length);
      mesh.userData = { family: fam };
      scene.add(mesh);
      instancedMeshes[fam] = mesh;
      raycastTargets.push(mesh);
    });

    // Crisp selection beacon ring that glides onto whichever node is selected.
    const selectRingGeom = new THREE.RingGeometry(1.45, 1.72, 48);
    const selectRingMat = new THREE.MeshBasicMaterial({
      color: 0x38bdf8,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.9,
    });
    const selectRing = new THREE.Mesh(selectRingGeom, selectRingMat);
    selectRing.visible = false;
    scene.add(selectRing);

    // 3. Single Merged Semantic Edge Buffer (THREE.LineSegments — 1 draw call!)
    const validEdges: EdgeSegmentMeta[] = [];
    for (const edge of edges) {
      const u = indexById.get(edge.source);
      const v = indexById.get(edge.target);
      if (u === undefined || v === undefined || u === v) continue;
      const isContainment =
        edge.relation === 'contains' || edge.relation === 'has_version';
      validEdges.push({
        sourceIdx: u,
        targetIdx: v,
        sourceId: edge.source,
        targetId: edge.target,
        relation: edge.relation,
        baseColor: new THREE.Color(edgeHexColors[edge.relation] ?? 0x64748b),
        baseIntensity: isContainment ? 0.34 : 0.85,
      });
    }

    const totalSegVerts = validEdges.length * CURVE_SEGMENTS * 2;
    const edgePosBuf = new Float32Array(Math.max(3, totalSegVerts * 3));
    const edgeColBuf = new Float32Array(Math.max(3, totalSegVerts * 3));
    const edgeGeom = new THREE.BufferGeometry();
    const edgePosAttr = new THREE.BufferAttribute(edgePosBuf, 3);
    const edgeColAttr = new THREE.BufferAttribute(edgeColBuf, 3);
    edgeGeom.setAttribute('position', edgePosAttr);
    edgeGeom.setAttribute('color', edgeColAttr);
    edgeGeom.setDrawRange(0, totalSegVerts);

    const edgeMat = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.82,
    });
    const edgeLines = new THREE.LineSegments(edgeGeom, edgeMat);
    scene.add(edgeLines);

    const dummyObj = new THREE.Object3D();
    const tmpColor = new THREE.Color();
    const tmpMid = new THREE.Vector3();
    const tmpPrev = new THREE.Vector3();
    const tmpCurr = new THREE.Vector3();
    const bgSlate = new THREE.Color(0x060b16);

    const rebuildEdgePositions = () => {
      let ptr = 0;
      for (const meta of validEdges) {
        const p0 = nodePositions[meta.sourceIdx];
        const p2 = nodePositions[meta.targetIdx];
        // Clean gentle quadratic arc bowing slightly outward from origin for readability.
        tmpMid.addVectors(p0, p2).multiplyScalar(0.5);
        const dist = p0.distanceTo(p2);
        const bow = Math.min(3.8, dist * 0.14);
        const len = tmpMid.length();
        if (len > 0.01) {
          tmpMid.addScaledVector(tmpMid, bow / len);
        } else {
          tmpMid.y += bow;
        }

        for (let s = 0; s < CURVE_SEGMENTS; s++) {
          const t0 = s / CURVE_SEGMENTS;
          const t1 = (s + 1) / CURVE_SEGMENTS;

          const omt0 = 1 - t0;
          tmpPrev.set(
            omt0 * omt0 * p0.x + 2 * omt0 * t0 * tmpMid.x + t0 * t0 * p2.x,
            omt0 * omt0 * p0.y + 2 * omt0 * t0 * tmpMid.y + t0 * t0 * p2.y,
            omt0 * omt0 * p0.z + 2 * omt0 * t0 * tmpMid.z + t0 * t0 * p2.z,
          );
          const omt1 = 1 - t1;
          tmpCurr.set(
            omt1 * omt1 * p0.x + 2 * omt1 * t1 * tmpMid.x + t1 * t1 * p2.x,
            omt1 * omt1 * p0.y + 2 * omt1 * t1 * tmpMid.y + t1 * t1 * p2.y,
            omt1 * omt1 * p0.z + 2 * omt1 * t1 * tmpMid.z + t1 * t1 * p2.z,
          );

          edgePosBuf[ptr] = tmpPrev.x;
          edgePosBuf[ptr + 1] = tmpPrev.y;
          edgePosBuf[ptr + 2] = tmpPrev.z;
          edgePosBuf[ptr + 3] = tmpCurr.x;
          edgePosBuf[ptr + 4] = tmpCurr.y;
          edgePosBuf[ptr + 5] = tmpCurr.z;
          ptr += 6;
        }
      }
      edgePosAttr.needsUpdate = true;
      edgeGeom.computeBoundingSphere();
    };

    const syncVisuals = () => {
      const selId = selectedIdRef.current;
      const hovId = hoveredIdRef.current;
      const lineage = focusedLineageRef.current;
      const progressGate = timelineRef.current / 100;

      let selectedPos: THREE.Vector3 | null = null;
      let selectedScale = 1;

      for (let i = 0; i < nodes.length; i++) {
        const node = nodes[i];
        const { family, slot } = nodeFamilySlot[i];
        const mesh = instancedMeshes[family];
        if (!mesh) continue;

        const pos = nodePositions[i];
        const isSelected = selId === node.id;
        const isHovered = hovId === node.id;
        const inLineage = lineage ? lineage.has(node.id) : true;
        const isScrubVisible = nodeProgress[i] <= progressGate;

        const baseR = effectiveRadius(node);
        const scale = !isScrubVisible
          ? 0.001
          : isSelected
            ? baseR * 1.38
            : isHovered
              ? baseR * 1.2
              : lineage && !inLineage
                ? baseR * 0.65
                : baseR;

        dummyObj.position.copy(pos);
        dummyObj.scale.setScalar(scale);
        dummyObj.rotation.set(
          family === 'octahedron' ? 0.35 : 0,
          i * 0.4,
          0,
        );
        dummyObj.updateMatrix();
        mesh.setMatrixAt(slot, dummyObj.matrix);

        tmpColor.setHex(kindColors[node.kind] ?? 0x60a5fa);
        const isQuietOutlineSection =
          node.kind === 'Section' && equationSectionIds.size > 0 && !equationSectionIds.has(node.id);
        if (isSelected) {
          tmpColor.lerp(new THREE.Color(0xffffff), 0.55);
          selectedPos = pos;
          selectedScale = baseR;
        } else if (isHovered) {
          tmpColor.lerp(new THREE.Color(0xffffff), 0.3);
        } else if (!isScrubVisible || (lineage && !inLineage)) {
          tmpColor.lerp(bgSlate, 0.82);
        } else if (isQuietOutlineSection) {
          tmpColor.lerp(bgSlate, 0.58);
        }
        mesh.setColorAt(slot, tmpColor);
      }

      for (const mesh of raycastTargets) {
        mesh.instanceMatrix.needsUpdate = true;
        if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
        mesh.computeBoundingSphere();
      }

      if (selectedPos) {
        selectRing.visible = true;
        selectRing.position.copy(selectedPos);
        selectRing.scale.setScalar(selectedScale);
      } else {
        selectRing.visible = false;
      }

      // Update merged edge vertex colors in place for lineage focus & timeline growth.
      let cPtr = 0;
      for (const meta of validEdges) {
        const isScrubVisible =
          Math.max(nodeProgress[meta.sourceIdx], nodeProgress[meta.targetIdx]) <=
          progressGate;
        const inLineage = lineage
          ? lineage.has(meta.sourceId) && lineage.has(meta.targetId)
          : true;
        const touchesSelection =
          selId !== null && (meta.sourceId === selId || meta.targetId === selId);

        let weight: number;
        if (!isScrubVisible) {
          weight = 0.02;
        } else if (touchesSelection) {
          weight = 1.0;
        } else if (lineage) {
          weight = inLineage ? 0.92 : 0.07;
        } else {
          weight = meta.baseIntensity;
        }

        tmpColor.copy(bgSlate).lerp(meta.baseColor, weight);
        for (let v = 0; v < CURVE_SEGMENTS * 2; v++) {
          edgeColBuf[cPtr] = tmpColor.r;
          edgeColBuf[cPtr + 1] = tmpColor.g;
          edgeColBuf[cPtr + 2] = tmpColor.b;
          cPtr += 3;
        }
      }
      edgeColAttr.needsUpdate = true;
    };

    syncVisualsRef.current = syncVisuals;

    const recomputeCameraFraming = () => {
      let maxR = 14;
      let minX = 0;
      let maxX = 0;
      for (const p of nodePositions) {
        const d = p.length();
        if (d > maxR) maxR = d;
        if (p.x < minX) minX = p.x;
        if (p.x > maxX) maxX = p.x;
      }
      const dist = THREE.MathUtils.clamp(maxR * 2.15, 28, 130);
      framingPresetsRef.current = {
        overview: { target: new THREE.Vector3(0, 0, 0), theta: 0.62, phi: 1.15, radius: dist },
        roots: {
          target: new THREE.Vector3(minX * 0.55, 0, 0),
          theta: -0.35,
          phi: 1.22,
          radius: Math.max(20, dist * 0.62),
        },
        canopy: {
          target: new THREE.Vector3(maxX * 0.55, 4.5, 0),
          theta: 1.18,
          phi: 0.9,
          radius: Math.max(22, dist * 0.68),
        },
      };
      orbitRef.current.desiredTarget.copy(framingPresetsRef.current.overview.target);
      orbitRef.current.target.copy(framingPresetsRef.current.overview.target);
      orbitRef.current.desiredRadius = dist;
      orbitRef.current.radius = dist;
    };

    rebuildEdgePositions();
    syncVisuals();
    recomputeCameraFraming();

    // 4. Async Rust WebAssembly Worker refinement (zero UI flicker!).
    let wasmCancelled = false;
    void computeLayout3DAsync(layoutInput).then((result) => {
      if (wasmCancelled) return;
      for (let i = 0; i < nodes.length; i++) {
        nodePositions[i].set(
          result.nodeXYZP[i * 4] ?? 0,
          result.nodeXYZP[i * 4 + 1] ?? 0,
          result.nodeXYZP[i * 4 + 2] ?? 0,
        );
        nodeProgress[i] = result.nodeXYZP[i * 4 + 3] ?? 0.5;
      }
      rebuildEdgePositions();
      syncVisuals();
      recomputeCameraFraming();
      setLayoutEngine(result.engine);
    });

    // 5. Screen-projected 2D HTML hub labels (Paper, equation-bearing Sections, Hypotheses — collision culled).
    const labelAnchorIndices: number[] = [];
    nodes.forEach((node, idx) => {
      const isKeySection =
        node.kind === 'Section' &&
        (equationSectionIds.size === 0 || equationSectionIds.has(node.id));
      if (
        node.kind === 'Paper' ||
        node.kind === 'PaperVersion' ||
        isKeySection ||
        node.kind === 'Hypothesis'
      ) {
        labelAnchorIndices.push(idx);
      }
    });
    // For tiny graphs with no sections, label up to 6 equations so the scene is readable.
    if (labelAnchorIndices.length < 3) {
      nodes.forEach((node, idx) => {
        if (node.kind === 'Equation' && labelAnchorIndices.length < 7) {
          labelAnchorIndices.push(idx);
        }
      });
    }

    const labelElements = new Map<number, HTMLDivElement>();
    const labelsLayer = labelsLayerRef.current;
    if (labelsLayer) {
      labelsLayer.replaceChildren();
      for (const idx of labelAnchorIndices.slice(0, 14)) {
        const node = nodes[idx];
        const el = document.createElement('div');
        el.className = `spatial-hub-label is-kind-${node.kind.toLowerCase()}`;
        const badge = document.createElement('span');
        badge.className = 'spatial-hub-label-kind';
        badge.textContent =
          node.kind === 'PaperVersion'
            ? 'PAPER'
            : node.kind.slice(0, 3).toUpperCase();
        const text = document.createElement('span');
        text.className = 'spatial-hub-label-text';
        text.textContent =
          node.label.length > 26 ? `${node.label.slice(0, 25)}…` : node.label;
        el.appendChild(badge);
        el.appendChild(text);
        labelsLayer.appendChild(el);
        labelElements.set(idx, el);
      }
    }

    // 6. True 360° Spherical Orbit + Shift/Right-Drag Pan + Smooth Zoom Controller
    let dragMode: 'none' | 'orbit' | 'pan' = 'none';
    let lastPtrX = 0;
    let lastPtrY = 0;
    let dragDistance = 0;

    const domElement = renderer.domElement;
    domElement.style.cursor = 'grab';

    const raycaster = new THREE.Raycaster();
    const pointerNdc = new THREE.Vector2(-10, -10);

    const pickNodeUnderPointer = (clientX: number, clientY: number): number | null => {
      const rect = domElement.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return null;
      pointerNdc.x = ((clientX - rect.left) / rect.width) * 2 - 1;
      pointerNdc.y = -((clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointerNdc, camera);

      const hits = raycaster.intersectObjects(raycastTargets, false);
      for (const hit of hits) {
        if (hit.instanceId === undefined) continue;
        const fam = hit.object.userData.family as MeshFamily | undefined;
        if (!fam) continue;
        const nodeIdx = familyMembers[fam][hit.instanceId];
        if (nodeIdx !== undefined && nodeProgress[nodeIdx] <= timelineRef.current / 100) {
          return nodeIdx;
        }
      }
      return null;
    };

    const onContextMenu = (evt: MouseEvent) => {
      evt.preventDefault();
    };

    const onPointerDown = (evt: PointerEvent) => {
      dragMode = evt.button === 2 || evt.shiftKey ? 'pan' : 'orbit';
      lastPtrX = evt.clientX;
      lastPtrY = evt.clientY;
      dragDistance = 0;
      domElement.style.cursor = 'grabbing';
      try {
        domElement.setPointerCapture(evt.pointerId);
      } catch {
        // Ignore in jsdom/synthetic pointer events.
      }
    };

    const panRight = new THREE.Vector3();
    const panUp = new THREE.Vector3();

    const onPointerMove = (evt: PointerEvent) => {
      if (dragMode !== 'none') {
        const dx = evt.clientX - lastPtrX;
        const dy = evt.clientY - lastPtrY;
        lastPtrX = evt.clientX;
        lastPtrY = evt.clientY;
        dragDistance += Math.hypot(dx, dy);

        const orbit = orbitRef.current;
        if (dragMode === 'orbit') {
          orbit.desiredTheta -= dx * 0.0072;
          orbit.desiredPhi = THREE.MathUtils.clamp(
            orbit.desiredPhi - dy * 0.0068,
            0.16,
            Math.PI - 0.16,
          );
        } else {
          const panScale = orbit.radius * 0.0014;
          panRight.setFromMatrixColumn(camera.matrix, 0);
          panUp.setFromMatrixColumn(camera.matrix, 1);
          orbit.desiredTarget.addScaledVector(panRight, -dx * panScale);
          orbit.desiredTarget.addScaledVector(panUp, dy * panScale);
        }
        return;
      }

      const hitIdx = pickNodeUnderPointer(evt.clientX, evt.clientY);
      const nextHover = hitIdx !== null ? nodes[hitIdx] : null;
      const nextId = nextHover?.id ?? null;
      if (hoveredIdRef.current !== nextId) {
        hoveredIdRef.current = nextId;
        setHoveredNode(nextHover);
        syncVisuals();
      }
      domElement.style.cursor = nextHover ? 'pointer' : 'grab';
    };

    const onPointerUp = (evt: PointerEvent) => {
      dragMode = 'none';
      domElement.style.cursor = hoveredIdRef.current ? 'pointer' : 'grab';
      try {
        domElement.releasePointerCapture(evt.pointerId);
      } catch {
        // Ignore in jsdom.
      }
    };

    const onPointerCancel = () => {
      dragMode = 'none';
      domElement.style.cursor = 'grab';
    };

    const onWheel = (evt: WheelEvent) => {
      evt.preventDefault();
      const orbit = orbitRef.current;
      const factor = Math.exp(evt.deltaY * 0.0012);
      orbit.desiredRadius = THREE.MathUtils.clamp(orbit.desiredRadius * factor, 10, 210);
    };

    const onClick = (evt: MouseEvent) => {
      if (dragDistance > 6) return;
      const hitIdx = pickNodeUnderPointer(evt.clientX, evt.clientY);
      if (hitIdx !== null) {
        const targetNode = nodes[hitIdx];
        onSelectRef.current(targetNode.id);
        setFocusActive(true);
        const orbit = orbitRef.current;
        orbit.desiredTarget.copy(nodePositions[hitIdx]);
        orbit.desiredRadius = THREE.MathUtils.clamp(
          framingPresetsRef.current.overview.radius * 0.56,
          16,
          64,
        );
      }
    };

    domElement.addEventListener('contextmenu', onContextMenu);
    domElement.addEventListener('pointerdown', onPointerDown);
    domElement.addEventListener('pointermove', onPointerMove);
    domElement.addEventListener('pointerup', onPointerUp);
    domElement.addEventListener('pointercancel', onPointerCancel);
    domElement.addEventListener('wheel', onWheel, { passive: false });
    domElement.addEventListener('click', onClick);

    const onResize = () => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      if (w === 0 || h === 0) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    };

    let resizeRafId: number | null = null;
    const resizeObserver = new ResizeObserver(() => {
      if (resizeRafId !== null) cancelAnimationFrame(resizeRafId);
      resizeRafId = requestAnimationFrame(() => {
        onResize();
        resizeRafId = null;
      });
    });
    resizeObserver.observe(container);

    // 7. Smooth 60 FPS Render & Label Projection Loop
    const projVec = new THREE.Vector3();
    let animId = 0;

    const animate = () => {
      animId = requestAnimationFrame(animate);
      const orbit = orbitRef.current;

      if (!reducedMotionRef.current && autoRotateRef.current && dragMode === 'none') {
        orbit.desiredTheta += 0.0024;
      }

      const damp = reducedMotionRef.current ? 1 : 0.1;
      orbit.theta = THREE.MathUtils.lerp(orbit.theta, orbit.desiredTheta, damp);
      orbit.phi = THREE.MathUtils.lerp(orbit.phi, orbit.desiredPhi, damp);
      orbit.radius = THREE.MathUtils.lerp(orbit.radius, orbit.desiredRadius, damp);
      orbit.target.lerp(orbit.desiredTarget, damp);

      const sinPhi = Math.sin(orbit.phi);
      camera.position.set(
        orbit.target.x + orbit.radius * sinPhi * Math.sin(orbit.theta),
        orbit.target.y + orbit.radius * Math.cos(orbit.phi),
        orbit.target.z + orbit.radius * sinPhi * Math.cos(orbit.theta),
      );
      camera.lookAt(orbit.target);

      if (selectRing.visible) {
        selectRing.lookAt(camera.position);
      }

      renderer.render(scene, camera);

      // Update projected 2D hub labels with screen-space collision culling (zero React renders!).
      if (labelElements.size > 0) {
        const cw = container.clientWidth || width;
        const ch = container.clientHeight || height;
        const placedBoxes: Array<[number, number, number, number]> = [];
        const progressGate = timelineRef.current / 100;
        const lineage = focusedLineageRef.current;

        for (const [idx, el] of labelElements) {
          const visible = nodeProgress[idx] <= progressGate;
          if (!visible) {
            el.style.opacity = '0';
            continue;
          }

          projVec.copy(nodePositions[idx]);
          projVec.y += (kindRadius[nodes[idx].kind] ?? 1) + 0.85;
          projVec.project(camera);

          if (projVec.z > 1 || projVec.z < -1) {
            el.style.opacity = '0';
            continue;
          }

          const sx = (projVec.x * 0.5 + 0.5) * cw;
          const sy = (-projVec.y * 0.5 + 0.5) * ch;
          const margin = 48;
          if (sx < margin || sx > cw - margin || sy < 56 || sy > ch - 52) {
            el.style.opacity = '0';
            continue;
          }

          const boxW = 148;
          const boxH = 26;
          const collides = placedBoxes.some(
            ([bx, by, bw, bh]) =>
              Math.abs(sx - bx) < (boxW + bw) * 0.48 &&
              Math.abs(sy - by) < (boxH + bh) * 0.52,
          );
          if (collides) {
            el.style.opacity = '0';
            continue;
          }

          placedBoxes.push([sx, sy, boxW, boxH]);
          const isFocus = !lineage || lineage.has(nodes[idx].id);
          el.style.transform = `translate3d(${sx.toFixed(1)}px, ${sy.toFixed(1)}px, 0) translate(-50%, -100%)`;
          el.style.opacity = isFocus ? '0.94' : '0.2';
        }
      }
    };
    animate();

    return () => {
      wasmCancelled = true;
      cancelAnimationFrame(animId);
      if (resizeRafId !== null) cancelAnimationFrame(resizeRafId);
      resizeObserver.disconnect();
      domElement.removeEventListener('contextmenu', onContextMenu);
      domElement.removeEventListener('pointerdown', onPointerDown);
      domElement.removeEventListener('pointermove', onPointerMove);
      domElement.removeEventListener('pointerup', onPointerUp);
      domElement.removeEventListener('pointercancel', onPointerCancel);
      domElement.removeEventListener('wheel', onWheel);
      domElement.removeEventListener('click', onClick);

      scene.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        if (mesh.geometry) mesh.geometry.dispose();
        if (mesh.material) {
          if (Array.isArray(mesh.material)) {
            mesh.material.forEach((m) => m.dispose());
          } else {
            mesh.material.dispose();
          }
        }
      });
      renderer.dispose();
      scene.clear();
      if (labelsLayer) labelsLayer.replaceChildren();
      if (domElement.parentElement) {
        domElement.parentElement.removeChild(domElement);
      }
    };
  }, [nodes, edges]);

  // Lightweight state updates without touching WebGLRenderer.
  useEffect(() => {
    syncVisualsRef.current();
  }, [selectedId, timelineProgress, focusedLineage]);

  const handleCameraPreset = useCallback((preset: CameraPreset) => {
    const p = framingPresetsRef.current[preset];
    const orbit = orbitRef.current;
    orbit.desiredTarget.copy(p.target);
    orbit.desiredTheta = p.theta;
    orbit.desiredPhi = p.phi;
    orbit.desiredRadius = p.radius;
  }, []);

  const handleResetCamera = useCallback(() => {
    handleCameraPreset('overview');
    setFocusActive(false);
  }, [handleCameraPreset]);

  const hoveredLatexHtml =
    hoveredNode?.kind === 'Equation'
      ? renderFormulaHtml(hoveredNode.expression)
      : null;

  return (
    <div className="graph-3d-viewport">
      <div ref={containerRef} className="spatial-canvas" />
      <div ref={labelsLayerRef} className="spatial-label-layer" aria-hidden="true" />

      {/* Keyboard Accessibility for 3D Navigation */}
      <button
        type="button"
        className="spatial-keyboard-control sr-only focus:not-sr-only"
        aria-label="3D Evidence Graph navigation. Press Arrow Left or Right to cycle nodes."
        onKeyDown={(e) => {
          if (nodes.length === 0) return;
          const currentIndex = nodes.findIndex((n) => n.id === selectedId);
          if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
            e.preventDefault();
            const nextIndex = (currentIndex + 1) % nodes.length;
            onSelect(nodes[nextIndex].id);
            setFocusActive(true);
          } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
            e.preventDefault();
            const prevIndex = (currentIndex - 1 + nodes.length) % nodes.length;
            onSelect(nodes[prevIndex].id);
            setFocusActive(true);
          }
        }}
      >
        Navigate 3D Nodes (Arrow keys)
      </button>

      <div className="sr-only" aria-live="polite">
        {selectedId
          ? `Selected evidence node: ${nodes.find((n) => n.id === selectedId)?.label ?? selectedId}`
          : 'No evidence node selected in 3D graph'}
      </div>

      <nav className="spatial-control-dock" aria-label="3D graph controls">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="spatial-control-button"
          onClick={() => setAutoRotate((prev) => !prev)}
          title={autoRotate ? 'Pause automatic orbit' : 'Resume automatic orbit'}
          aria-pressed={autoRotate}
        >
          {autoRotate ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
          {autoRotate ? 'Pause' : 'Rotate'}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="spatial-control-button"
          onClick={handleResetCamera}
          title="Reset camera and clear branch focus"
        >
          <RotateCcw size={14} aria-hidden="true" />
          Reset
        </Button>
        {isFullscreen ? (
          <div className="spatial-preset-group" aria-label="Camera presets">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="spatial-preset-button"
              onClick={() => handleCameraPreset('roots')}
              title="View origin"
            >
              Origin
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="spatial-preset-button"
              onClick={() => handleCameraPreset('overview')}
              title="Overview"
            >
              Overview
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="spatial-preset-button"
              onClick={() => handleCameraPreset('canopy')}
              title="View frontier"
            >
              Frontier
            </Button>
          </div>
        ) : null}
      </nav>

      <div className="spatial-status-card">
        <span className="spatial-status-icon" aria-hidden="true">
          <Sparkles size={14} />
        </span>
        <span>
          <strong>3D lineage</strong>
          <small>
            {nodes.length} nodes · {edges.length} links
            {layoutEngine === 'rust-wasm' ? ' · WASM' : ''}
          </small>
        </span>
        {layoutEngine === 'rust-wasm' ? (
          <span className="spatial-wasm-pill" title="Computed by Rust WebAssembly Worker">
            <Cpu size={11} aria-hidden="true" />
          </span>
        ) : null}
        {focusActive && selectedId ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="spatial-clear-focus"
            onClick={() => setFocusActive(false)}
            title="Show all branches"
          >
            <X size={12} aria-hidden="true" />
            Clear focus
          </Button>
        ) : null}
      </div>

      {isFullscreen ? (
        <div className="spatial-timeline-control">
          <TreePine size={16} aria-hidden="true" />
          <div>
            <div className="spatial-timeline-label">
              <span>Origin</span>
              <strong>Timeline growth: {timelineProgress}%</strong>
              <span>Frontier</span>
            </div>
            <Slider
              value={[timelineProgress]}
              min={15}
              max={100}
              step={1}
              onValueChange={(val) =>
                setTimelineProgress(Array.isArray(val) ? val[0] : (val as number))
              }
              aria-label="Timeline growth"
            />
          </div>
        </div>
      ) : null}

      <div className="spatial-legend-bar" aria-hidden="true">
        <span><i className="is-paper" />Paper</span>
        <span><i className="is-section" />Section</span>
        <span><i className="is-equation" />Equation</span>
        <span><i className="is-symbol" />Symbol</span>
        <span><i className="is-hypothesis" />Hypothesis</span>
      </div>

      <span className="spatial-gesture-hint" aria-hidden="true">
        Drag to orbit 360° · Shift+drag pan · Scroll to zoom
      </span>

      {hoveredNode ? (
        <div className="spatial-hover-card">
          <div>
            <span>{hoveredNode.kind}</span>
            <small>{hoveredNode.meta}</small>
          </div>
          <strong>{hoveredNode.label}</strong>
          {hoveredLatexHtml ? (
            <div
              className="spatial-hover-formula"
              dangerouslySetInnerHTML={{ __html: hoveredLatexHtml }}
            />
          ) : (
            <code>{hoveredNode.expression}</code>
          )}
          <p>
            <Focus size={12} aria-hidden="true" /> Click to orbit &amp; isolate branch
          </p>
        </div>
      ) : null}
    </div>
  );
}
