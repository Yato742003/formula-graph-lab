'use client';

import * as THREE from 'three';
import {
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
import { renderFormulaHtml } from '@/app/evidence-graph-viewport';
import type {
  EvidenceEntityType,
} from '@/lib/import-types';
import type {
  GraphViewportEdge,
  GraphViewportNode,
} from '@/app/evidence-graph-viewport';

const geomCache = new Map<string, THREE.BufferGeometry>();
function getMeshGeometry(kind: EvidenceEntityType, r: number): THREE.BufferGeometry {
  const key = `${kind}-${r}`;
  if (!geomCache.has(key)) {
    let geom: THREE.BufferGeometry;
    if (kind === 'Equation') {
      geom = new THREE.OctahedronGeometry(r, 0);
    } else if (kind === 'Symbol') {
      geom = new THREE.TetrahedronGeometry(r, 0);
    } else if (kind === 'Hypothesis') {
      geom = new THREE.DodecahedronGeometry(r, 0);
    } else {
      geom = new THREE.SphereGeometry(r, 20, 20);
    }
    geomCache.set(key, geom);
  }
  return geomCache.get(key)!;
}

type Graph3DViewportProps = {
  nodes: GraphViewportNode[];
  edges: GraphViewportEdge[];
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
  isFullscreen?: boolean;
};

// Loki "God of Stories" Sacred Timeline & Lightning Palette
const kindColors: Record<EvidenceEntityType, number> = {
  Paper: 0xffffff,        // Radiant white stellar nexus
  PaperVersion: 0x67e8f9, // Electric cyan nexus
  Section: 0x38bdf8,      // Neon sky blue
  Equation: 0x22d3ee,     // Bright electric cyan plasma
  Symbol: 0xa855f7,       // Cosmic violet spark
  Assumption: 0xf43f5e,   // Rose-magenta rune
  Claim: 0xc084fc,        // Ethereal purple
  Concept: 0x34d399,      // Emerald lightning
  Method: 0x38bdf8,       // Sky blue
  Experiment: 0x67e8f9,   // Frost cyan
  Hypothesis: 0xfbbf24,   // Golden lightning nexus
};

const kindRadius: Record<EvidenceEntityType, number> = {
  Paper: 15,
  PaperVersion: 12,
  Section: 7.5,
  Equation: 9,
  Symbol: 5,
  Assumption: 6,
  Claim: 6.5,
  Concept: 6.5,
  Method: 7,
  Experiment: 7,
  Hypothesis: 12,
};

type CameraPreset = 'overview' | 'roots' | 'canopy';

// Deterministic pseudo-random generator
function prng(seed: number) {
  const x = Math.sin(seed) * 10000;
  return x - Math.floor(x);
}

// ponytail: cylindrical camera distance calculation, ceiling: clamped between [45, 520], upgrade: dynamic FOV scaling if multi-paper corpus spans >100 nodes
function computeFramingDistance(
  sizeX: number,
  radiusYZ: number,
  aspect: number,
  fov: number,
  padding = 1.08,
): number {
  const vFovRad = (fov * Math.PI) / 360;
  const distY = radiusYZ / Math.tan(vFovRad);
  const distX = (sizeX / 2) / (aspect * Math.tan(vFovRad));
  const dist = Math.max(distX, distY) * padding;
  return Math.min(520, Math.max(45, dist));
}

export default function Graph3DViewport({
  nodes,
  edges,
  selectedId,
  onSelect,
  isFullscreen = false,
}: Graph3DViewportProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredNode, setHoveredNode] = useState<GraphViewportNode | null>(null);
  const [autoRotate, setAutoRotate] = useState(true);
  const [timelineProgress, setTimelineProgress] = useState(100); // 0% to 100%
  const [focusActive, setFocusActive] = useState(false);

  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const targetCamPosRef = useRef<THREE.Vector3 | null>(null);
  const resetRotationRef = useRef<() => void>(() => {});
  const framingRef = useRef({
    overview: new THREE.Vector3(0, 0, 340),
    roots: new THREE.Vector3(-100, 0, 240),
    canopy: new THREE.Vector3(100, 0, 240),
  });

  // Compute Ancestors & Descendants for Branch Focus Mode
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

    // Gather ancestors
    const queueUp = [selectedId];
    while (queueUp.length > 0) {
      const cur = queueUp.shift()!;
      const parents = backwardAdj.get(cur);
      if (parents) {
        for (const p of parents) {
          if (!result.has(p)) {
            result.add(p);
            queueUp.push(p);
          }
        }
      }
    }

    // Gather descendants
    const queueDown = [selectedId];
    while (queueDown.length > 0) {
      const cur = queueDown.shift()!;
      const children = forwardAdj.get(cur);
      if (children) {
        for (const c of children) {
          if (!result.has(c)) {
            result.add(c);
            queueDown.push(c);
          }
        }
      }
    }

    return result;
  }, [selectedId, focusActive, forwardAdj, backwardAdj]);

  const nodeMeshMapRef = useRef<
    Map<
      string,
      {
        mesh: THREE.Mesh;
        halo?: THREE.Mesh;
        mat: THREE.MeshStandardMaterial;
        baseColor: number;
        node: GraphViewportNode;
      }
    >
  >(new Map());
  const edgeVisualsRef = useRef<
    Array<{
      lineMat: THREE.LineBasicMaterial;
      source: string;
      target: string;
      relation: string;
    }>
  >([]);
  const nodeNormalizedProgressRef = useRef<Map<string, number>>(new Map());
  const onSelectRef = useRef(onSelect);
  const autoRotateRef = useRef(autoRotate);
  const reducedMotionRef = useRef(false);
  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);
  useEffect(() => {
    autoRotateRef.current = autoRotate;
  }, [autoRotate]);
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

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x020617); // Deep cosmic void
    scene.fog = new THREE.FogExp2(0x030712, 0.0008);

    // Root Group for the Entire Horizontal Timeline Tree (Rotates exclusively around X-axis)
    const treeGroup = new THREE.Group();
    scene.add(treeGroup);

    // Camera: fixed looking horizontally at the Sacred Timeline
    const width = container.clientWidth || 800;
    const height = container.clientHeight || 420;
    const camera = new THREE.PerspectiveCamera(46, width / height, 1, 4000);
    camera.position.set(0, 0, 420);
    cameraRef.current = camera;

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.replaceChildren(renderer.domElement);

    // Lighting (Electric cyan, white, and violet ambient & directional lights)
    const ambientLight = new THREE.AmbientLight(0x0e7490, 0.7);
    scene.add(ambientLight);

    const dirLight1 = new THREE.DirectionalLight(0x38bdf8, 1.4);
    dirLight1.position.set(200, 300, 400);
    scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0xa855f7, 0.9);
    dirLight2.position.set(-200, -200, -300);
    scene.add(dirLight2);

    // 1. Cosmic Stardust Field (Electric Cyan & Violet Starlight)
    const starCount = 850;
    const starGeom = new THREE.BufferGeometry();
    const starPositions = new Float32Array(starCount * 3);
    const starColors = new Float32Array(starCount * 3);

    for (let i = 0; i < starCount * 3; i += 3) {
      starPositions[i] = (Math.random() - 0.5) * 2600;
      starPositions[i + 1] = (Math.random() - 0.5) * 2200;
      starPositions[i + 2] = (Math.random() - 0.5) * 2400;

      if (Math.random() > 0.45) {
        starColors[i] = 0.35;
        starColors[i + 1] = 0.85;
        starColors[i + 2] = 0.98; // Cyan
      } else {
        starColors[i] = 0.75;
        starColors[i + 1] = 0.45;
        starColors[i + 2] = 0.98; // Violet
      }
    }
    starGeom.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
    starGeom.setAttribute('color', new THREE.BufferAttribute(starColors, 3));
    const starMat = new THREE.PointsMaterial({
      size: 2.2,
      vertexColors: true,
      transparent: true,
      opacity: 0.7,
      blending: THREE.AdditiveBlending,
    });
    const starField = new THREE.Points(starGeom, starMat);
    scene.add(starField);

    // 2. Timeline Span & Paper Coordinates (Adaptive based on paper count)
    const paperNodes = nodes.filter((n) => n.kind === 'Paper' || n.kind === 'PaperVersion');
    const paperCount = Math.max(1, paperNodes.length);
    const totalSpanX = paperCount === 1 ? 0 : Math.min(340, Math.max(140, (paperCount - 1) * 90));
    const halfSpanX = totalSpanX / 2;
    const trunkMinX = paperCount === 1 ? -80 : -halfSpanX - 25;
    const trunkMaxX = paperCount === 1 ? 80 : halfSpanX + 25;

    // Horizontal Anamorphic Light Streak (Loki Timeline River Plane)
    const flareGeom = new THREE.PlaneGeometry(Math.max(160, (trunkMaxX - trunkMinX) + 50), 14);
    const flareMat = new THREE.MeshBasicMaterial({
      color: 0x0284c7,
      transparent: true,
      opacity: 0.15,
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
    });
    const flareMesh = new THREE.Mesh(flareGeom, flareMat);
    flareMesh.position.set(0, 0, 0);
    treeGroup.add(flareMesh);

    // 3. Node Coordinates Calculation (Chronological X-Axis Flow)
    const nodeCoords = new Map<string, THREE.Vector3>();
    const nodeNormalizedProgress = new Map<string, number>(); // 0 (past/left) to 1 (future/right)

    // Position Paper Hubs along the Horizontal Sacred Timeline (Left: past -> Right: present)
    paperNodes.forEach((p, idx) => {
      const t = paperCount === 1 ? 0.5 : idx / (paperCount - 1);
      const x = paperCount === 1 ? 0 : -halfSpanX + t * totalSpanX;
      const y = Math.sin(t * Math.PI * 2) * 12;
      const z = Math.cos(t * Math.PI * 2) * 10;
      const pos = new THREE.Vector3(x, y, z);
      nodeCoords.set(p.id, pos);
      nodeNormalizedProgress.set(p.id, t);
    });

    // Trace child containment
    const childToParent = new Map<string, string>();
    edges.forEach((edge) => {
      if (
        edge.relation === 'contains' ||
        edge.relation === 'defines' ||
        edge.relation === 'has_version'
      ) {
        childToParent.set(edge.target, edge.source);
      }
    });

    const getRootPaperPos = (nodeId: string, depth = 0): THREE.Vector3 => {
      if (depth > 6) return new THREE.Vector3(0, 0, 0);
      if (nodeCoords.has(nodeId)) return nodeCoords.get(nodeId)!;
      const parentId = childToParent.get(nodeId);
      if (parentId) return getRootPaperPos(parentId, depth + 1);
      return new THREE.Vector3(0, 0, 0);
    };

    // Distribute Equations, Sections, Symbols branching outward (upward Y>0, downward Y<0, and in Z)
    const nonPaperNodes = nodes.filter((n) => n.kind !== 'Paper' && n.kind !== 'PaperVersion');
    const nonPapersByKind = new Map<EvidenceEntityType, GraphViewportNode[]>();
    nonPaperNodes.forEach((n) => {
      const list = nonPapersByKind.get(n.kind) ?? [];
      list.push(n);
      nonPapersByKind.set(n.kind, list);
    });

    const GOLDEN_ANGLE = 2.399963; // ~137.5 degrees

    const kindBranchConfig: Record<string, { rSpread: number; xForward: number }> = {
      Section: { rSpread: 36, xForward: 14 },
      Equation: { rSpread: 58, xForward: 24 },
      Symbol: { rSpread: 80, xForward: 34 },
      Assumption: { rSpread: 28, xForward: -10 },
      Claim: { rSpread: 64, xForward: 20 },
      Hypothesis: { rSpread: 92, xForward: 38 },
    };

    nonPapersByKind.forEach((kindList, kind) => {
      const config = kindBranchConfig[kind] ?? { rSpread: 50, xForward: 16 };
      const count = kindList.length;

      kindList.forEach((node, kIdx) => {
        const parentPos = getRootPaperPos(node.id);

        // ponytail: deterministic golden angle spiral layout, ceiling: max 240-unit timeline span, upgrade: 3D force simulation if papers interlock densely
        const phi = kIdx * GOLDEN_ANGLE;
        const radialVar = 0.82 + 0.36 * (kIdx % 4);
        const r = config.rSpread * radialVar;

        const ySpread = Math.sin(phi) * r;
        const zSpread = Math.cos(phi) * r;

        // Spread longitudinally along timeline X relative to parent paper
        const maxSpan = Math.min(240, Math.max(90, count * 8.5));
        const xOffset = count > 1
          ? ((kIdx / (count - 1)) - 0.5) * maxSpan
          : 0;
        const xSpread = parentPos.x + config.xForward + xOffset;

        const pos = new THREE.Vector3(xSpread, parentPos.y + ySpread, parentPos.z + zSpread);
        nodeCoords.set(node.id, pos);

        const normProg = THREE.MathUtils.clamp(
          (xSpread - trunkMinX) / Math.max(1, trunkMaxX - trunkMinX),
          0,
          1,
        );
        nodeNormalizedProgress.set(node.id, normProg);
      });
    });

    // 4. Braided Sacred Timeline Trunk (Horizontal Glowing River)
    const trunkGroup = new THREE.Group();
    treeGroup.add(trunkGroup);

    const trunkStrandCount = 5;
    const trunkSteps = 50;
    const minX = trunkMinX;
    const maxX = trunkMaxX;

    for (let s = 0; s < trunkStrandCount; s++) {
      const phase = (s / trunkStrandCount) * Math.PI * 2;
      const strandPoints: THREE.Vector3[] = [];
      const strandColors: number[] = [];

      for (let step = 0; step <= trunkSteps; step++) {
        const u = step / trunkSteps;
        const x = minX + u * (maxX - minX);
        const wave = Math.sin(u * Math.PI * 4 + phase);
        const cosWave = Math.cos(u * Math.PI * 4 + phase);
        const y = wave * (7 + Math.sin(u * Math.PI) * 11);
        const z = cosWave * (7 + Math.sin(u * Math.PI) * 11);
        strandPoints.push(new THREE.Vector3(x, y, z));

        const color = new THREE.Color(s === 0 ? 0xffffff : 0x67e8f9);
        strandColors.push(color.r, color.g, color.b);
      }

      const geom = new THREE.BufferGeometry().setFromPoints(strandPoints);
      geom.setAttribute('color', new THREE.Float32BufferAttribute(strandColors, 3));
      const mat = new THREE.LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.85,
        blending: THREE.AdditiveBlending,
      });
      trunkGroup.add(new THREE.Line(geom, mat));
    }

    // 5. Branching Lightning Curves with Jitter & Capillary Tendrils
    const vineGroup = new THREE.Group();
    treeGroup.add(vineGroup);
    const pulseCurves: THREE.Curve<THREE.Vector3>[] = [];

    edges.forEach((edge, eIdx) => {
      const srcPos = nodeCoords.get(edge.source);
      const tgtPos = nodeCoords.get(edge.target);
      if (!srcPos || !tgtPos) return;

      const opacity = edge.relation === 'derived_from' || edge.relation === 'cites' ? 0.85 : 0.55;

      // Arc with natural organic curve
      const dist = srcPos.distanceTo(tgtPos);
      const mid = new THREE.Vector3().addVectors(srcPos, tgtPos).multiplyScalar(0.5);
      const dir = new THREE.Vector3().subVectors(tgtPos, srcPos).normalize();
      const perp = new THREE.Vector3(-dir.y, dir.x, dir.z * 0.5).normalize();
      mid.addScaledVector(perp, Math.min(42, dist * 0.22));

      const curve = new THREE.QuadraticBezierCurve3(srcPos, mid, tgtPos);
      pulseCurves.push(curve);

      // Create lightning points with electric jitter
      const numSegments = 16;
      const points: THREE.Vector3[] = [];
      const colors: number[] = [];

      for (let i = 0; i <= numSegments; i++) {
        const u = i / numSegments;
        const pt = curve.getPoint(u);

        // Electric jitter: zero at endpoints, max at middle
        const jitterWeight = Math.sin(u * Math.PI) * Math.min(11, dist * 0.09);
        const jX = (prng(eIdx * 100 + i) - 0.5) * jitterWeight;
        const jY = (prng(eIdx * 100 + i + 1) - 0.5) * jitterWeight;
        const jZ = (prng(eIdx * 100 + i + 2) - 0.5) * jitterWeight;
        points.push(new THREE.Vector3(pt.x + jX, pt.y + jY, pt.z + jZ));

        // Color Gradient: White at root -> Cyan at mid -> Violet at tips
        const col = new THREE.Color();
        if (u < 0.4) {
          col.setHex(0xffffff).lerp(new THREE.Color(0x67e8f9), u / 0.4);
        } else {
          col.setHex(0x38bdf8).lerp(new THREE.Color(0xa855f7), (u - 0.4) / 0.6);
        }
        colors.push(col.r, col.g, col.b);
      }

      const geom = new THREE.BufferGeometry().setFromPoints(points);
      geom.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
      const lineMat = new THREE.LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity,
        blending: THREE.AdditiveBlending,
      });
      const lineMesh = new THREE.Line(geom, lineMat);
      vineGroup.add(lineMesh);
      edgeVisualsRef.current.push({
        lineMat,
        source: edge.source,
        target: edge.target,
        relation: edge.relation,
      });

      // Fine Capillary Tendrils (small branching sparks off the main limb)
      if (dist > 35) {
        const forkU = 0.55 + prng(eIdx * 37) * 0.3;
        const forkRoot = curve.getPoint(forkU);
        const forkDir = new THREE.Vector3(
          (prng(eIdx * 41) - 0.5) * 22,
          (prng(eIdx * 43) - 0.2) * 26,
          (prng(eIdx * 47) - 0.5) * 22,
        );
        const tendrilPoints = [
          forkRoot,
          new THREE.Vector3()
            .addVectors(forkRoot, forkDir.clone().multiplyScalar(0.5))
            .add(new THREE.Vector3((prng(eIdx) - 0.5) * 4, 3, (prng(eIdx + 1) - 0.5) * 4)),
          new THREE.Vector3().addVectors(forkRoot, forkDir),
        ];
        const tendrilGeom = new THREE.BufferGeometry().setFromPoints(tendrilPoints);
        const tendrilMat = new THREE.LineBasicMaterial({
          color: 0xc084fc,
          transparent: true,
          opacity: opacity * 0.65,
          blending: THREE.AdditiveBlending,
        });
        vineGroup.add(new THREE.Line(tendrilGeom, tendrilMat));
      }
    });

    // 6. Glowing Nodes with Atmospheric Halos & Distinct Geometries
    const nodeMeshMap = new Map<
      string,
      {
        mesh: THREE.Mesh;
        halo?: THREE.Mesh;
        mat: THREE.MeshStandardMaterial;
        baseColor: number;
        node: GraphViewportNode;
      }
    >();

    nodes.forEach((node) => {
      const pos = nodeCoords.get(node.id) ?? new THREE.Vector3(0, 0, 0);
      const radius = kindRadius[node.kind] ?? 8;
      const baseColor = kindColors[node.kind] ?? 0x22d3ee;
      const opacity = 0.92;
      const emissiveColor = baseColor;
      const emissiveIntensity = 0.45;

      const mat = new THREE.MeshStandardMaterial({
        color: baseColor,
        emissive: emissiveColor,
        emissiveIntensity: emissiveIntensity,
        roughness: 0.22,
        metalness: 0.35,
        transparent: true,
        opacity,
      });

      const mesh = new THREE.Mesh(getMeshGeometry(node.kind, radius), mat);
      mesh.position.copy(pos);
      mesh.userData = { nodeId: node.id, node };
      treeGroup.add(mesh);

      let haloMesh: THREE.Mesh | undefined;
      // Glowing Cosmic Halo for Papers and Hypotheses
      if (node.kind === 'Paper' || node.kind === 'Hypothesis') {
        const haloGeom = new THREE.RingGeometry(radius * 1.35, radius * 1.7, 32);
        const haloMat = new THREE.MeshBasicMaterial({
          color: baseColor,
          side: THREE.DoubleSide,
          transparent: true,
          opacity: opacity * 0.75,
          blending: THREE.AdditiveBlending,
        });
        haloMesh = new THREE.Mesh(haloGeom, haloMat);
        haloMesh.rotation.x = Math.PI / 2;
        mesh.add(haloMesh);
      }

      nodeMeshMap.set(node.id, { mesh, halo: haloMesh, mat, baseColor, node });
    });
    nodeMeshMapRef.current = nodeMeshMap;
    nodeNormalizedProgressRef.current = nodeNormalizedProgress;

    // 7. Traveling Electric Sparks (Mana Charges)
    const particleCount = Math.min(140, Math.max(20, pulseCurves.length * 2));
    const particleGeom = new THREE.BufferGeometry();
    const particlePositions = new Float32Array(particleCount * 3);
    particleGeom.setAttribute('position', new THREE.BufferAttribute(particlePositions, 3));
    const particleMat = new THREE.PointsMaterial({
      color: 0xffffff,
      size: 3.8,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending,
    });
    const particlePoints = new THREE.Points(particleGeom, particleMat);
    treeGroup.add(particlePoints);

    const particleProgress = Array.from({ length: particleCount }, () => Math.random());

    // 8. Dynamic Frustum Framing: Bounding Cylinder along horizontal timeline axis
    let bMinX = Infinity, bMaxX = -Infinity;
    let maxRadiusYZ = 0;

    nodeCoords.forEach((pos) => {
      if (pos.x < bMinX) bMinX = pos.x;
      if (pos.x > bMaxX) bMaxX = pos.x;
      const r = Math.sqrt(pos.y * pos.y + pos.z * pos.z);
      if (r > maxRadiusYZ) maxRadiusYZ = r;
    });

    if (bMinX === Infinity) {
      bMinX = -50; bMaxX = 50; maxRadiusYZ = 35;
    }

    const sizeX = Math.max(50, (bMaxX - bMinX) + 24);
    const radiusYZ = Math.max(30, maxRadiusYZ + 14);
    const boxCenter = new THREE.Vector3((bMinX + bMaxX) / 2, 0, 0);

    const optimalDistance = computeFramingDistance(sizeX, radiusYZ, width / height, camera.fov, 1.08);
    camera.position.set(boxCenter.x, 0, optimalDistance);

    framingRef.current = {
      overview: new THREE.Vector3(boxCenter.x, 0, optimalDistance),
      roots: new THREE.Vector3(Math.min(bMinX * 0.85, -30), 0, Math.max(50, optimalDistance * 0.72)),
      canopy: new THREE.Vector3(Math.max(bMaxX * 0.85, 30), 0, Math.max(50, optimalDistance * 0.72)),
    };

    // 9. Custom Axis Rotation Controller (Rotates Exclusively Around Horizontal X-Axis)
    let isDragging = false;
    let pointerStartY = 0;
    let dragDeltaTotal = 0;
    let targetRotationX = 0;
    let currentRotationX = 0;

    resetRotationRef.current = () => {
      targetRotationX = 0;
    };

    const domElement = renderer.domElement;
    domElement.style.cursor = 'grab';

    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2(-1000, -1000);

    const onPointerDown = (evt: PointerEvent) => {
      isDragging = true;
      pointerStartY = evt.clientY;
      dragDeltaTotal = 0;
      domElement.style.cursor = 'grabbing';
      try {
        domElement.setPointerCapture(evt.pointerId);
      } catch {}
    };

    const onPointerMove = (evt: PointerEvent) => {
      if (isDragging) {
        const delta = evt.clientY - pointerStartY;
        pointerStartY = evt.clientY;
        dragDeltaTotal += Math.abs(delta);
        targetRotationX += delta * 0.009; // Rolls tree around horizontal X-axis
      }

      const rect = domElement.getBoundingClientRect();
      mouse.x = ((evt.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((evt.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const meshes = [...nodeMeshMap.values()].map((entry) => entry.mesh);
      const intersects = raycaster.intersectObjects(meshes);

      if (intersects.length > 0) {
        const hit = intersects[0].object;
        const targetNode = hit.userData.node as GraphViewportNode;
        setHoveredNode(targetNode);
        domElement.style.cursor = isDragging ? 'grabbing' : 'pointer';
      } else {
        setHoveredNode(null);
        domElement.style.cursor = isDragging ? 'grabbing' : 'grab';
      }
    };

    const onPointerUp = (evt: PointerEvent) => {
      isDragging = false;
      domElement.style.cursor = 'grab';
      try {
        domElement.releasePointerCapture(evt.pointerId);
      } catch {}
    };

    const onPointerCancel = () => {
      isDragging = false;
      domElement.style.cursor = 'grab';
    };

    // Zoom via mouse wheel
    const onWheel = (evt: WheelEvent) => {
      evt.preventDefault();
      camera.position.z = THREE.MathUtils.clamp(
        camera.position.z + evt.deltaY * 0.45,
        180,
        750
      );
    };

    const onClick = () => {
      if (dragDeltaTotal > 6) return; // Ignore drag release

      raycaster.setFromCamera(mouse, camera);
      const meshes = [...nodeMeshMap.values()].map((entry) => entry.mesh);
      const intersects = raycaster.intersectObjects(meshes);

      if (intersects.length > 0) {
        const hit = intersects[0].object;
        const targetNode = hit.userData.node as GraphViewportNode;
        onSelectRef.current(targetNode.id);
        setFocusActive(true);

        const targetPos = nodeCoords.get(targetNode.id);
        if (targetPos) {
          const focusDist = THREE.MathUtils.clamp(optimalDistance * 0.68, 40, 260);
          targetCamPosRef.current = new THREE.Vector3(targetPos.x, 0, focusDist);
        }
      }
    };

    domElement.addEventListener('pointerdown', onPointerDown);
    domElement.addEventListener('pointermove', onPointerMove);
    domElement.addEventListener('pointerup', onPointerUp);
    domElement.addEventListener('pointercancel', onPointerCancel);
    domElement.addEventListener('wheel', onWheel, { passive: false });
    domElement.addEventListener('click', onClick);

    const onResize = () => {
      if (!container) return;
      const w = container.clientWidth;
      const h = container.clientHeight;
      if (w === 0 || h === 0) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

      const newDist = computeFramingDistance(sizeX, radiusYZ, w / h, camera.fov, 1.08);
      framingRef.current.overview.set(boxCenter.x, 0, newDist);
      targetCamPosRef.current = new THREE.Vector3(boxCenter.x, 0, newDist);
    };

    const resizeObserver = new ResizeObserver(() => {
      onResize();
    });
    resizeObserver.observe(container);

    // Animation Loop
    let animId: number;
    const animate = () => {
      animId = requestAnimationFrame(animate);

      // Auto-rotation strictly around horizontal X-axis
      if (!reducedMotionRef.current && autoRotateRef.current && !isDragging) {
        targetRotationX += 0.0055;
      }

      // Smooth inertia lerp around X-axis
      currentRotationX = THREE.MathUtils.lerp(currentRotationX, targetRotationX, 0.08);
      treeGroup.rotation.x = currentRotationX;

      // Spin node halos
      if (!reducedMotionRef.current) {
        nodeMeshMap.forEach(({ halo }) => {
          if (halo) halo.rotation.z += 0.014;
        });
      }

      // Flowing electric sparks along curves
      if (!reducedMotionRef.current && pulseCurves.length > 0) {
        const positions = particleGeom.attributes.position.array as Float32Array;
        for (let i = 0; i < particleCount; i++) {
          particleProgress[i] = (particleProgress[i] + 0.008) % 1.0;
          const curveIdx = i % pulseCurves.length;
          const point = pulseCurves[curveIdx].getPoint(particleProgress[i]);
          positions[i * 3] = point.x;
          positions[i * 3 + 1] = point.y;
          positions[i * 3 + 2] = point.z;
        }
        particleGeom.attributes.position.needsUpdate = true;
      }

      // Smooth camera position glide
      if (targetCamPosRef.current) {
        if (reducedMotionRef.current) {
          camera.position.copy(targetCamPosRef.current);
        } else {
          camera.position.lerp(targetCamPosRef.current, 0.06);
        }
        if (camera.position.distanceTo(targetCamPosRef.current) < 2) {
          targetCamPosRef.current = null;
        }
      }

      renderer.render(scene, camera);
    };
    animate();

    return () => {
      cancelAnimationFrame(animId);
      resizeObserver.disconnect();
      domElement.removeEventListener('pointerdown', onPointerDown);
      domElement.removeEventListener('pointermove', onPointerMove);
      domElement.removeEventListener('pointerup', onPointerUp);
      domElement.removeEventListener('pointercancel', onPointerCancel);
      domElement.removeEventListener('wheel', onWheel);
      domElement.removeEventListener('click', onClick);

      // Deep GPU resource disposal across entire scene graph
      scene.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        if (mesh.geometry) {
          mesh.geometry.dispose();
        }
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
      nodeMeshMapRef.current.clear();
      edgeVisualsRef.current = [];
      if (domElement.parentElement) {
        domElement.parentElement.removeChild(domElement);
      }
    };
  }, [nodes, edges]);

  // Lightweight visual state updates without rebuilding WebGL scene
  useEffect(() => {
    nodeMeshMapRef.current.forEach(({ mesh, halo, mat, baseColor, node }) => {
      const isSelected = selectedId === node.id;
      const inLineage = focusedLineage ? focusedLineage.has(node.id) : true;
      const normProg = nodeNormalizedProgressRef.current.get(node.id) ?? 0;
      const isScrubVisible = normProg <= timelineProgress / 100;

      const opacity = !isScrubVisible
        ? 0.05
        : focusedLineage
          ? inLineage ? 1.0 : 0.12
          : 0.92;

      mat.opacity = opacity;
      mat.color.setHex(isSelected ? 0xffffff : baseColor);
      mat.emissive.setHex(isSelected ? 0xffffff : baseColor);
      mat.emissiveIntensity = isSelected ? 1.2 : inLineage ? 0.65 : 0.15;

      if (halo) {
        halo.visible = isSelected || node.kind === 'Paper' || node.kind === 'Hypothesis';
        const haloMat = halo.material as THREE.MeshBasicMaterial;
        haloMat.color.setHex(isSelected ? 0xffffff : baseColor);
        haloMat.opacity = opacity * 0.75;
      }
      mesh.scale.setScalar(isSelected ? 1.28 : 1.0);
    });

    edgeVisualsRef.current.forEach(({ lineMat, source, target, relation }) => {
      const inLineage = focusedLineage
        ? focusedLineage.has(source) && focusedLineage.has(target)
        : true;
      const srcProg = nodeNormalizedProgressRef.current.get(source) ?? 0;
      const tgtProg = nodeNormalizedProgressRef.current.get(target) ?? 0;
      const isScrubVisible = Math.max(srcProg, tgtProg) <= timelineProgress / 100;

      lineMat.opacity = !isScrubVisible
        ? 0.03
        : focusedLineage
          ? inLineage ? 1.0 : 0.08
          : relation === 'derived_from' || relation === 'cites' ? 0.85 : 0.55;
    });
  }, [selectedId, timelineProgress, focusedLineage]);

  // Camera Preset Switcher (strictly horizontal targets)
  const handleCameraPreset = useCallback((preset: CameraPreset) => {
    switch (preset) {
      case 'overview':
        targetCamPosRef.current = framingRef.current.overview.clone();
        break;
      case 'roots':
        targetCamPosRef.current = framingRef.current.roots.clone();
        break;
      case 'canopy':
        targetCamPosRef.current = framingRef.current.canopy.clone();
        break;
    }
  }, []);

  const handleResetCamera = useCallback(() => {
    handleCameraPreset('overview');
    resetRotationRef.current();
    setFocusActive(false);
  }, [handleCameraPreset]);

  const hoveredLatexHtml =
    hoveredNode?.kind === 'Equation'
      ? renderFormulaHtml(hoveredNode.expression)
      : null;

  return (
    <div className="graph-3d-viewport">
      <div ref={containerRef} className="spatial-canvas" />

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
          title={autoRotate ? 'Pause automatic rotation' : 'Resume automatic rotation'}
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
          <small>{nodes.length} nodes · {edges.length} links</small>
        </span>

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

      <span className="spatial-gesture-hint" aria-hidden="true">
        Drag to orbit · Scroll to zoom
      </span>

      {hoveredNode ? (
        <div className="spatial-hover-card">
          <div>
            <span>
              {hoveredNode.kind}
            </span>
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
            <Focus size={12} aria-hidden="true" /> Select to isolate this branch
          </p>
        </div>
      ) : null}
    </div>
  );
}
