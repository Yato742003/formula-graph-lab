export type LayoutEngineKind = 'rust-wasm' | 'ts-fallback';

export type Layout2DParams = {
  lanePadX: number;
  lanePadTop: number;
  lanePadBottom: number;
  cardGapX: number;
  cardGapY: number;
  laneGapX: number;
  laneGapY: number;
  maxLanesPerRow: number;
};

export type Layout2DInput = {
  nodeGroupIds: Uint32Array;
  nodeKindRanks: Uint32Array;
  nodeWidths: Float32Array;
  nodeHeights: Float32Array;
  edgeSrcs: Uint32Array;
  edgeDsts: Uint32Array;
  edgeWeights: Float32Array;
  groupCount: number;
  groupMaxCols: Uint32Array;
  groupBaryFlags: Uint32Array;
  params: Layout2DParams;
};

export type Layout2DResult = {
  /** Flat [x0, y0, x1, y1, ...] in canvas coordinates. */
  nodeXY: Float32Array;
  /** Flat [x, y, width, height, ...] per group. */
  groupRects: Float32Array;
  engine: LayoutEngineKind;
  durationMs: number;
};

export type Layout3DInput = {
  /** 0=Paper, 1=Section, 2=Equation, 3=Symbol, 4=Concept/Method/Assumption, 5=Claim/Hypothesis */
  nodeKinds: Uint32Array;
  /** Parent index in 0..n-1, or -1 if root. */
  nodeParents: Int32Array;
  /** Owning paper slot index in 0..paperCount-1. */
  nodeRootPapers: Int32Array;
  edgeSrcs: Uint32Array;
  edgeDsts: Uint32Array;
  /** 0=contains, 1=derived_from, 2=defines, 3=uses, 4=cites/disagrees, 5=other */
  edgeKindCodes: Uint32Array;
};

export type Layout3DResult = {
  /** Flat [x0, y0, z0, progress0, ...] centered at origin. */
  nodeXYZP: Float32Array;
  engine: LayoutEngineKind;
  durationMs: number;
};

export interface FormulaLayoutWasmExports extends WebAssembly.Exports {
  memory: WebAssembly.Memory;
  wasm_alloc: (byteLen: number) => number;
  wasm_dealloc: (ptr: number, byteLen: number) => void;
  wasm_layout_2d: (
    nodeCount: number,
    groupCount: number,
    edgeCount: number,
    nodeGroupIdsPtr: number,
    nodeKindRanksPtr: number,
    nodeWidthsPtr: number,
    nodeHeightsPtr: number,
    edgeSrcsPtr: number,
    edgeDstsPtr: number,
    edgeWeightsPtr: number,
    groupMaxColsPtr: number,
    groupBaryFlagsPtr: number,
    paramsPtr: number,
    outNodeXYPtr: number,
    outGroupRectsPtr: number,
  ) => number;
  wasm_layout_3d: (
    nodeCount: number,
    edgeCount: number,
    nodeKindsPtr: number,
    nodeParentsPtr: number,
    nodeRootPapersPtr: number,
    edgeSrcsPtr: number,
    edgeDstsPtr: number,
    edgeKindCodesPtr: number,
    outXYZPPtr: number,
  ) => number;
}

const TAU = Math.PI * 2;

function nowMs(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

export async function instantiateLayoutWasm(
  bytes: BufferSource,
): Promise<FormulaLayoutWasmExports> {
  const compiled = await WebAssembly.compile(bytes);
  const instance = await WebAssembly.instantiate(compiled, {});
  return instance.exports as unknown as FormulaLayoutWasmExports;
}

export function runWasmLayout2D(
  exports: FormulaLayoutWasmExports,
  input: Layout2DInput,
): Layout2DResult {
  const t0 = nowMs();
  const n = input.nodeGroupIds.length;
  const g = input.groupCount;
  const e = input.edgeSrcs.length;

  if (n === 0 || g === 0) {
    return {
      nodeXY: new Float32Array(0),
      groupRects: new Float32Array(0),
      engine: 'rust-wasm',
      durationMs: 0,
    };
  }

  const allocations: Array<[number, number]> = [];
  const allocCopyU32 = (arr: Uint32Array): number => {
    if (arr.length === 0) return 0;
    const bytes = arr.byteLength;
    const ptr = exports.wasm_alloc(bytes);
    allocations.push([ptr, bytes]);
    new Uint32Array(exports.memory.buffer, ptr, arr.length).set(arr);
    return ptr;
  };
  const allocCopyF32 = (arr: Float32Array): number => {
    if (arr.length === 0) return 0;
    const bytes = arr.byteLength;
    const ptr = exports.wasm_alloc(bytes);
    allocations.push([ptr, bytes]);
    new Float32Array(exports.memory.buffer, ptr, arr.length).set(arr);
    return ptr;
  };
  const allocEmptyF32 = (len: number): number => {
    const bytes = len * 4;
    const ptr = exports.wasm_alloc(bytes);
    allocations.push([ptr, bytes]);
    return ptr;
  };

  try {
    const paramsArr = new Float32Array([
      input.params.lanePadX,
      input.params.lanePadTop,
      input.params.lanePadBottom,
      input.params.cardGapX,
      input.params.cardGapY,
      input.params.laneGapX,
      input.params.laneGapY,
      input.params.maxLanesPerRow,
    ]);

    const pGroupIds = allocCopyU32(input.nodeGroupIds);
    const pKindRanks = allocCopyU32(input.nodeKindRanks);
    const pWidths = allocCopyF32(input.nodeWidths);
    const pHeights = allocCopyF32(input.nodeHeights);
    const pEdgeSrcs = allocCopyU32(input.edgeSrcs);
    const pEdgeDsts = allocCopyU32(input.edgeDsts);
    const pEdgeWeights = allocCopyF32(input.edgeWeights);
    const pMaxCols = allocCopyU32(input.groupMaxCols);
    const pBaryFlags = allocCopyU32(input.groupBaryFlags);
    const pParams = allocCopyF32(paramsArr);
    const pOutXY = allocEmptyF32(n * 2);
    const pOutRects = allocEmptyF32(g * 4);

    const ok = exports.wasm_layout_2d(
      n,
      g,
      e,
      pGroupIds,
      pKindRanks,
      pWidths,
      pHeights,
      pEdgeSrcs,
      pEdgeDsts,
      pEdgeWeights,
      pMaxCols,
      pBaryFlags,
      pParams,
      pOutXY,
      pOutRects,
    );

    if (ok !== 1) {
      throw new Error('Rust WASM wasm_layout_2d returned failure code');
    }

    // Copy out of WASM linear memory before deallocating.
    const nodeXY = new Float32Array(
      new Float32Array(exports.memory.buffer, pOutXY, n * 2),
    );
    const groupRects = new Float32Array(
      new Float32Array(exports.memory.buffer, pOutRects, g * 4),
    );

    return {
      nodeXY,
      groupRects,
      engine: 'rust-wasm',
      durationMs: Math.max(0, nowMs() - t0),
    };
  } finally {
    for (const [ptr, bytes] of allocations) {
      if (ptr !== 0) exports.wasm_dealloc(ptr, bytes);
    }
  }
}

export function runWasmLayout3D(
  exports: FormulaLayoutWasmExports,
  input: Layout3DInput,
): Layout3DResult {
  const t0 = nowMs();
  const n = input.nodeKinds.length;
  const e = input.edgeSrcs.length;

  if (n === 0) {
    return {
      nodeXYZP: new Float32Array(0),
      engine: 'rust-wasm',
      durationMs: 0,
    };
  }

  const allocations: Array<[number, number]> = [];
  const allocCopyU32 = (arr: Uint32Array): number => {
    if (arr.length === 0) return 0;
    const bytes = arr.byteLength;
    const ptr = exports.wasm_alloc(bytes);
    allocations.push([ptr, bytes]);
    new Uint32Array(exports.memory.buffer, ptr, arr.length).set(arr);
    return ptr;
  };
  const allocCopyI32 = (arr: Int32Array): number => {
    if (arr.length === 0) return 0;
    const bytes = arr.byteLength;
    const ptr = exports.wasm_alloc(bytes);
    allocations.push([ptr, bytes]);
    new Int32Array(exports.memory.buffer, ptr, arr.length).set(arr);
    return ptr;
  };

  try {
    const pKinds = allocCopyU32(input.nodeKinds);
    const pParents = allocCopyI32(input.nodeParents);
    const pRoots = allocCopyI32(input.nodeRootPapers);
    const pEdgeSrcs = allocCopyU32(input.edgeSrcs);
    const pEdgeDsts = allocCopyU32(input.edgeDsts);
    const pEdgeKinds = allocCopyU32(input.edgeKindCodes);
    const outBytes = n * 4 * 4;
    const pOut = exports.wasm_alloc(outBytes);
    allocations.push([pOut, outBytes]);

    const ok = exports.wasm_layout_3d(
      n,
      e,
      pKinds,
      pParents,
      pRoots,
      pEdgeSrcs,
      pEdgeDsts,
      pEdgeKinds,
      pOut,
    );

    if (ok !== 1) {
      throw new Error('Rust WASM wasm_layout_3d returned failure code');
    }

    const nodeXYZP = new Float32Array(
      new Float32Array(exports.memory.buffer, pOut, n * 4),
    );
    return {
      nodeXYZP,
      engine: 'rust-wasm',
      durationMs: Math.max(0, nowMs() - t0),
    };
  } finally {
    for (const [ptr, bytes] of allocations) {
      if (ptr !== 0) exports.wasm_dealloc(ptr, bytes);
    }
  }
}

/**
 * Deterministic TypeScript mirror of `crates/formula-layout-wasm` for instant
 * first-paint SSR/jsdom layout while the Web Worker loads the Rust WASM binary.
 */
export function computeLayout2DFallback(input: Layout2DInput): Layout2DResult {
  const t0 = nowMs();
  const n = input.nodeGroupIds.length;
  const gCount = input.groupCount;
  const outXY = new Float32Array(n * 2);
  const outRects = new Float32Array(gCount * 4);

  if (n === 0 || gCount === 0) {
    return { nodeXY: outXY, groupRects: outRects, engine: 'ts-fallback', durationMs: 0 };
  }

  const lanePadX = Math.max(8, input.params.lanePadX);
  const lanePadTop = Math.max(24, input.params.lanePadTop);
  const lanePadBottom = Math.max(12, input.params.lanePadBottom);
  const cardGapX = Math.max(12, input.params.cardGapX);
  const cardGapY = Math.max(10, input.params.cardGapY);
  const laneGapX = Math.max(24, input.params.laneGapX);
  const laneGapY = Math.max(24, input.params.laneGapY);
  const maxLanesPerRow = Math.max(1, Math.round(input.params.maxLanesPerRow));

  const groups: number[][] = Array.from({ length: gCount }, () => []);
  for (let i = 0; i < n; i++) {
    const g = Math.min(input.nodeGroupIds[i] ?? 0, gCount - 1);
    groups[g].push(i);
  }
  for (const members of groups) {
    members.sort((a, b) => (input.nodeKindRanks[a] - input.nodeKindRanks[b]) || (a - b));
  }

  const adj: Array<Array<[number, number]>> = Array.from({ length: n }, () => []);
  const edgeLen = Math.min(input.edgeSrcs.length, input.edgeDsts.length, input.edgeWeights.length);
  for (let e = 0; e < edgeLen; e++) {
    const u = input.edgeSrcs[e];
    const v = input.edgeDsts[e];
    if (u < n && v < n && u !== v) {
      const w = Math.max(0.25, input.edgeWeights[e]);
      adj[u].push([v, w]);
      adj[v].push([u, w]);
    }
  }

  const rankPos = new Float32Array(n);
  for (let pass = 0; pass < 2; pass++) {
    for (const members of groups) {
      const len = Math.max(1, members.length);
      for (let pos = 0; pos < members.length; pos++) {
        rankPos[members[pos]] = (pos + 0.5) / len;
      }
    }
    for (let g = 0; g < gCount; g++) {
      const members = groups[g];
      if ((input.groupBaryFlags[g] ?? 0) === 0 || members.length <= 2) continue;
      const scored = members.map((nodeIdx, origPos) => {
        let sum = 0;
        let wsum = 0;
        for (const [nb, w] of adj[nodeIdx]) {
          if (input.nodeGroupIds[nb] !== g) {
            sum += rankPos[nb] * w;
            wsum += w;
          }
        }
        const bary = wsum > 0 ? sum / wsum : (origPos + 0.5) / members.length;
        return { nodeIdx, kind: input.nodeKindRanks[nodeIdx], bary, origPos };
      });
      scored.sort((a, b) => (a.kind - b.kind) || (a.bary - b.bary) || (a.origPos - b.origPos));
      for (let k = 0; k < members.length; k++) members[k] = scored[k].nodeIdx;
    }
  }

  type LocalLane = {
    groupId: number;
    width: number;
    height: number;
    placements: Array<[number, number, number]>;
  };

  const localLanes: LocalLane[] = [];
  for (let g = 0; g < gCount; g++) {
    const members = groups[g];
    if (members.length === 0) {
      localLanes.push({ groupId: g, width: 0, height: 0, placements: [] });
      continue;
    }
    const maxCols = Math.max(1, input.groupMaxCols[g] ?? 1);
    const cols =
      members.length <= 1 || maxCols === 1
        ? 1
        : Math.min(members.length, Math.max(1, Math.min(maxCols, Math.ceil(members.length / 5))));

    let totalH = 0;
    for (const idx of members) totalH += Math.max(44, input.nodeHeights[idx]) + cardGapY;
    const targetColH = Math.max(1, totalH / cols);

    const buckets: number[][] = Array.from({ length: cols }, () => []);
    let curCol = 0;
    let curH = 0;
    for (let pos = 0; pos < members.length; pos++) {
      const idx = members[pos];
      const itemH = Math.max(44, input.nodeHeights[idx]) + cardGapY;
      const remainingItems = members.length - pos;
      const remainingCols = cols - curCol;
      if (
        curCol + 1 < cols &&
        buckets[curCol].length > 0 &&
        (curH + itemH * 0.5 > targetColH || remainingItems < remainingCols)
      ) {
        curCol++;
        curH = 0;
      }
      buckets[curCol].push(idx);
      curH += itemH;
    }

    const nonEmpty = buckets.filter((b) => b.length > 0);
    const colWidths = nonEmpty.map((bucket) =>
      bucket.reduce((mx, idx) => Math.max(mx, Math.max(160, input.nodeWidths[idx])), 220),
    );

    const placements: Array<[number, number, number]> = [];
    let cursorX = lanePadX;
    let maxColBottom = lanePadTop;

    for (let c = 0; c < nonEmpty.length; c++) {
      const colW = colWidths[c];
      let cursorY = lanePadTop;
      for (const idx of nonEmpty[c]) {
        const cardW = Math.max(160, input.nodeWidths[idx]);
        const cardH = Math.max(44, input.nodeHeights[idx]);
        const centeredX = cursorX + Math.max(0, colW - cardW) * 0.5;
        placements.push([idx, centeredX, cursorY]);
        cursorY += cardH + cardGapY;
      }
      maxColBottom = Math.max(maxColBottom, Math.max(lanePadTop, cursorY - cardGapY));
      cursorX += colW + cardGapX;
    }

    localLanes.push({
      groupId: g,
      width: cursorX - cardGapX + lanePadX,
      height: maxColBottom + lanePadBottom,
      placements,
    });
  }

  const groupXY: Array<[number, number]> = Array.from({ length: gCount }, () => [0, 0]);
  const hasRootLane = localLanes[0].placements.length > 0;
  const contentStartX = hasRootLane ? localLanes[0].width + laneGapX + 28 : 0;
  const contentIds: number[] = [];
  for (let g = 1; g < gCount; g++) {
    if (localLanes[g].placements.length > 0) contentIds.push(g);
  }

  let bandY = 0;
  let firstBandH = 0;
  for (let start = 0; start < contentIds.length; start += maxLanesPerRow) {
    const chunk = contentIds.slice(start, start + maxLanesPerRow);
    let cursorX = contentStartX;
    let bandMaxH = 0;
    for (const g of chunk) {
      groupXY[g] = [cursorX, bandY];
      cursorX += localLanes[g].width + laneGapX;
      bandMaxH = Math.max(bandMaxH, localLanes[g].height);
    }
    if (start === 0) firstBandH = bandMaxH;
    bandY += bandMaxH + laneGapY;
  }

  if (hasRootLane) {
    const rootY =
      firstBandH > localLanes[0].height
        ? Math.max(0, (firstBandH - localLanes[0].height) * 0.25)
        : 0;
    groupXY[0] = [0, rootY];
  }

  for (const lane of localLanes) {
    const g = lane.groupId;
    const [gx, gy] = groupXY[g];
    outRects[g * 4] = gx;
    outRects[g * 4 + 1] = gy;
    outRects[g * 4 + 2] = lane.width;
    outRects[g * 4 + 3] = lane.height;
    for (const [idx, lx, ly] of lane.placements) {
      outXY[idx * 2] = gx + lx;
      outXY[idx * 2 + 1] = gy + ly;
    }
  }

  return {
    nodeXY: outXY,
    groupRects: outRects,
    engine: 'ts-fallback',
    durationMs: Math.max(0, nowMs() - t0),
  };
}

export function computeLayout3DFallback(input: Layout3DInput): Layout3DResult {
  const t0 = nowMs();
  const n = input.nodeKinds.length;
  const out = new Float32Array(n * 4);
  if (n === 0) {
    return { nodeXYZP: out, engine: 'ts-fallback', durationMs: 0 };
  }

  const pos: Array<[number, number, number]> = Array.from({ length: n }, () => [0, 0, 0]);
  const anchor: Array<[number, number, number]> = Array.from({ length: n }, () => [0, 0, 0]);
  const progress = new Float32Array(n).fill(0.5);

  const paperIndices: number[] = [];
  for (let i = 0; i < n; i++) {
    if (input.nodeKinds[i] === 0) paperIndices.push(i);
  }

  const paperPos: Array<[number, number, number]> = [];
  if (paperIndices.length === 0) {
    paperPos.push([0, 0, 0]);
  } else {
    const half = (paperIndices.length - 1) * 0.5;
    paperIndices.forEach((idx, slot) => {
      const p: [number, number, number] = [(slot - half) * 36, 0, 0];
      pos[idx] = [...p];
      anchor[idx] = [...p];
      progress[idx] = 0.03 + 0.08 * (slot / Math.max(1, paperIndices.length));
      paperPos.push(p);
    });
  }

  const rootCenter = (i: number): [number, number, number] => {
    const rp = input.nodeRootPapers[i] ?? 0;
    return rp >= 0 && rp < paperPos.length ? paperPos[rp] : paperPos[0];
  };

  const sectionHasEq = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    if (input.nodeKinds[i] === 2) {
      const p = input.nodeParents[i] ?? -1;
      if (p >= 0 && p < n && input.nodeKinds[p] === 1) {
        sectionHasEq[p] = 1;
      }
    }
  }

  const primarySectionsPerPaper: number[][] = Array.from({ length: paperPos.length }, () => []);
  const outlineSectionsPerPaper: number[][] = Array.from({ length: paperPos.length }, () => []);
  for (let i = 0; i < n; i++) {
    if (input.nodeKinds[i] === 1) {
      const slot = Math.max(0, Math.min(paperPos.length - 1, input.nodeRootPapers[i] ?? 0));
      if (sectionHasEq[i]) primarySectionsPerPaper[slot].push(i);
      else outlineSectionsPerPaper[slot].push(i);
    }
  }

  const nodeAngle = new Float32Array(n);
  const sectionWedge = new Float32Array(n).fill(TAU / 6);

  for (let slot = 0; slot < paperPos.length; slot++) {
    const center = paperPos[slot];
    const primary = primarySectionsPerPaper[slot];
    const outline = outlineSectionsPerPaper[slot];
    const useSplit = primary.length > 0 && outline.length >= 6;
    const ringList = useSplit ? primary : [...primary, ...outline];

    if (ringList.length > 0) {
      const ringR = Math.min(15.5, 11.0 + Math.sqrt(ringList.length) * 1.35);
      const wedge = TAU / ringList.length;
      ringList.forEach((idx, rank) => {
        const theta = rank * wedge + slot * 0.45;
        const p: [number, number, number] = [
          center[0] + Math.cos(theta) * ringR,
          center[1] + Math.sin(theta * 2) * 1.4,
          center[2] + Math.sin(theta) * ringR,
        ];
        pos[idx] = [...p];
        anchor[idx] = [...p];
        nodeAngle[idx] = theta;
        sectionWedge[idx] = wedge;
        progress[idx] = 0.14 + 0.22 * (rank / Math.max(1, ringList.length));
      });
    }

    if (useSplit && outline.length > 0) {
      const oWedge = TAU / outline.length;
      outline.forEach((idx, rank) => {
        const theta = rank * oWedge + 0.22;
        const tier = rank % 2;
        const r = 8.2 + tier * 3.4;
        const p: [number, number, number] = [
          center[0] + Math.cos(theta) * r,
          center[1] - 6.2 - tier * 1.2,
          center[2] + Math.sin(theta) * r,
        ];
        pos[idx] = [...p];
        anchor[idx] = [...p];
        nodeAngle[idx] = theta;
        sectionWedge[idx] = oWedge;
        progress[idx] = 0.2 + 0.16 * (rank / Math.max(1, outline.length));
      });
    }
  }

  const eqByParent = new Map<number, number[]>();
  for (let i = 0; i < n; i++) {
    if (input.nodeKinds[i] === 2) {
      const parent = input.nodeParents[i] ?? -1;
      const list = eqByParent.get(parent) ?? [];
      list.push(i);
      eqByParent.set(parent, list);
    }
  }

  for (const [parentId, eqList] of eqByParent) {
    const count = eqList.length;
    const hasSectionParent = parentId >= 0 && parentId < n && input.nodeKinds[parentId] === 1;
    if (hasSectionParent) {
      const center = rootCenter(parentId);
      const baseTheta = nodeAngle[parentId];
      const wedge = Math.min(1.15, sectionWedge[parentId] * 0.78);
      eqList.forEach((idx, rank) => {
        const tier = Math.floor(rank / 4);
        const inTier = rank % 4;
        const tierLen = Math.min(4, count - tier * 4);
        const frac = tierLen <= 1 ? 0 : inTier / (tierLen - 1) - 0.5;
        const theta = baseTheta + frac * wedge;
        const r = 19.5 + tier * 4.4;
        const y = pos[parentId][1] + (inTier - (tierLen - 1) * 0.5) * 1.9;
        const p: [number, number, number] = [
          center[0] + Math.cos(theta) * r,
          y,
          center[2] + Math.sin(theta) * r,
        ];
        pos[idx] = [...p];
        anchor[idx] = [...p];
        nodeAngle[idx] = theta;
        progress[idx] = 0.38 + 0.38 * (rank / Math.max(1, count));
      });
    } else {
      eqList.forEach((idx, rank) => {
        const center = rootCenter(idx);
        const frac = rank / Math.max(1, count);
        const theta = frac * TAU * (count > 10 ? 2 : 1) + 0.3;
        const ring = Math.floor(rank / 10);
        const r = 16.5 + ring * 5;
        const y = ((rank % 5) - 2) * 1.85;
        const p: [number, number, number] = [
          center[0] + Math.cos(theta) * r,
          center[1] + y,
          center[2] + Math.sin(theta) * r,
        ];
        pos[idx] = [...p];
        anchor[idx] = [...p];
        nodeAngle[idx] = theta;
        progress[idx] = 0.36 + 0.4 * frac;
      });
    }
  }

  const symByParent = new Map<number, number[]>();
  for (let i = 0; i < n; i++) {
    if (input.nodeKinds[i] === 3) {
      const parent = input.nodeParents[i] ?? -1;
      const list = symByParent.get(parent) ?? [];
      list.push(i);
      symByParent.set(parent, list);
    }
  }

  for (const [parentId, symList] of symByParent) {
    const count = symList.length;
    const validParent = parentId >= 0 && parentId < n;
    symList.forEach((idx, rank) => {
      const frac = rank / Math.max(1, count);
      let p: [number, number, number];
      if (validParent) {
        const pp = pos[parentId];
        const phi = nodeAngle[parentId] + frac * TAU + 0.5;
        const satR = 3.2 + Math.floor(rank / 6) * 1.4;
        const satY = ((rank % 3) - 1) * 1.25;
        p = [pp[0] + Math.cos(phi) * satR, pp[1] + satY, pp[2] + Math.sin(phi) * satR];
      } else {
        const center = rootCenter(idx);
        const theta = frac * TAU + 0.7;
        p = [center[0] + Math.cos(theta) * 23.5, center[1] - 4.2, center[2] + Math.sin(theta) * 23.5];
      }
      pos[idx] = [...p];
      anchor[idx] = [...p];
      progress[idx] = 0.76 + 0.22 * frac;
    });
  }

  const lowerNodes: number[] = [];
  const upperNodes: number[] = [];
  for (let i = 0; i < n; i++) {
    if (input.nodeKinds[i] === 4) lowerNodes.push(i);
    else if (input.nodeKinds[i] >= 5) upperNodes.push(i);
  }

  lowerNodes.forEach((idx, rank) => {
    const center = rootCenter(idx);
    const frac = rank / Math.max(1, lowerNodes.length);
    const theta = frac * TAU + 0.25;
    const r = 14.5 + (rank % 2) * 4.5;
    const p: [number, number, number] = [
      center[0] + Math.cos(theta) * r,
      center[1] - 7.2 - (rank % 3) * 0.9,
      center[2] + Math.sin(theta) * r,
    ];
    pos[idx] = [...p];
    anchor[idx] = [...p];
    progress[idx] = 0.62 + 0.28 * frac;
  });

  upperNodes.forEach((idx, rank) => {
    const center = rootCenter(idx);
    const frac = rank / Math.max(1, upperNodes.length);
    const theta = frac * TAU + 0.85;
    const r = 13.5 + (rank % 2) * 4.2;
    const p: [number, number, number] = [
      center[0] + Math.cos(theta) * r,
      center[1] + 8 + (rank % 3) * 1.1,
      center[2] + Math.sin(theta) * r,
    ];
    pos[idx] = [...p];
    anchor[idx] = [...p];
    progress[idx] = 0.74 + 0.24 * frac;
  });

  const minSepFor = (kind: number) => {
    if (kind === 0) return 6.0;
    if (kind === 1) return 4.4;
    if (kind === 2) return 3.5;
    if (kind === 3) return 2.1;
    return 3.2;
  };

  const edgeLen = Math.min(input.edgeSrcs.length, input.edgeDsts.length, input.edgeKindCodes.length);
  const iterations = n > 400 ? 24 : 44;
  const disp: Array<[number, number, number]> = Array.from({ length: n }, () => [0, 0, 0]);

  for (let iter = 0; iter < iterations; iter++) {
    for (let i = 0; i < n; i++) {
      disp[i][0] = 0;
      disp[i][1] = 0;
      disp[i][2] = 0;
    }
    const stepScale = 0.42 * (1 - iter / (iterations + 8));

    for (let i = 0; i < n; i++) {
      const sepI = minSepFor(input.nodeKinds[i]);
      for (let j = i + 1; j < n; j++) {
        const targetSep = (sepI + minSepFor(input.nodeKinds[j])) * 0.5;
        const dx = pos[i][0] - pos[j][0];
        const dy = pos[i][1] - pos[j][1];
        const dz = pos[i][2] - pos[j][2];
        const distSq = dx * dx + dy * dy + dz * dz;
        if (distSq < targetSep * targetSep) {
          let nx: number;
          let ny: number;
          let nz: number;
          let dist: number;
          if (distSq > 1e-6) {
            dist = Math.sqrt(distSq);
            nx = dx / dist;
            ny = dy / dist;
            nz = dz / dist;
          } else {
            const a = ((i * 73 + j * 37) % 360) * (TAU / 360);
            nx = Math.cos(a);
            ny = 0.25;
            nz = Math.sin(a);
            dist = 0.01;
          }
          const push = (targetSep - dist) * 0.48;
          disp[i][0] += nx * push;
          disp[i][1] += ny * push;
          disp[i][2] += nz * push;
          disp[j][0] -= nx * push;
          disp[j][1] -= ny * push;
          disp[j][2] -= nz * push;
        }
      }
    }

    for (let e = 0; e < edgeLen; e++) {
      const u = input.edgeSrcs[e];
      const v = input.edgeDsts[e];
      const code = input.edgeKindCodes[e];
      if (u < n && v < n && u !== v && (code === 1 || code === 4)) {
        const dx = pos[v][0] - pos[u][0];
        const dy = pos[v][1] - pos[u][1];
        const dz = pos[v][2] - pos[u][2];
        const dist = Math.max(0.01, Math.hypot(dx, dy, dz));
        if (dist > 7.5) {
          const pull = Math.min(6, dist - 7.5) * 0.035;
          const fx = (dx / dist) * pull;
          const fy = (dy / dist) * pull;
          const fz = (dz / dist) * pull;
          disp[u][0] += fx;
          disp[u][1] += fy;
          disp[u][2] += fz;
          disp[v][0] -= fx;
          disp[v][1] -= fy;
          disp[v][2] -= fz;
        }
      }
    }

    for (let i = 0; i < n; i++) {
      if (input.nodeKinds[i] === 0) continue;
      const tether = input.nodeKinds[i] === 1 ? 0.26 : 0.14;
      disp[i][0] += (anchor[i][0] - pos[i][0]) * tether;
      disp[i][1] += (anchor[i][1] - pos[i][1]) * tether;
      disp[i][2] += (anchor[i][2] - pos[i][2]) * tether;
      const mag = Math.hypot(disp[i][0], disp[i][1], disp[i][2]);
      const clamp = mag > 2.4 ? 2.4 / mag : 1;
      pos[i][0] += disp[i][0] * clamp * stepScale;
      pos[i][1] += disp[i][1] * clamp * stepScale;
      pos[i][2] += disp[i][2] * clamp * stepScale;
    }
  }

  let minX = Infinity;
  let minY = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let maxZ = -Infinity;
  for (const p of pos) {
    if (p[0] < minX) minX = p[0];
    if (p[1] < minY) minY = p[1];
    if (p[2] < minZ) minZ = p[2];
    if (p[0] > maxX) maxX = p[0];
    if (p[1] > maxY) maxY = p[1];
    if (p[2] > maxZ) maxZ = p[2];
  }
  const midX = (minX + maxX) * 0.5;
  const midY = (minY + maxY) * 0.5;
  const midZ = (minZ + maxZ) * 0.5;

  for (let i = 0; i < n; i++) {
    out[i * 4] = pos[i][0] - midX;
    out[i * 4 + 1] = pos[i][1] - midY;
    out[i * 4 + 2] = pos[i][2] - midZ;
    out[i * 4 + 3] = Math.max(0.02, Math.min(1, progress[i]));
  }

  return {
    nodeXYZP: out,
    engine: 'ts-fallback',
    durationMs: Math.max(0, nowMs() - t0),
  };
}

// ---------- Browser Web Worker Bridge ----------

type WorkerResponse =
  | { id: number; ok: true; kind: '2d'; result: Layout2DResult }
  | { id: number; ok: true; kind: '3d'; result: Layout3DResult }
  | { id: number; ok: false; error: string };

let sharedWorker: Worker | null = null;
let workerUnavailable = false;
let nextRequestId = 1;
const pending = new Map<
  number,
  { resolve: (value: Layout2DResult | Layout3DResult) => void; reject: (err: Error) => void }
>();

function getLayoutWorker(): Worker | null {
  if (workerUnavailable || typeof window === 'undefined' || typeof Worker === 'undefined') {
    return null;
  }
  if (!sharedWorker) {
    try {
      sharedWorker = new Worker(new URL('./graph-layout.worker.ts', import.meta.url), {
        type: 'module',
      });
      sharedWorker.addEventListener('message', (event: MessageEvent<WorkerResponse>) => {
        const msg = event.data;
        const entry = pending.get(msg.id);
        if (!entry) return;
        pending.delete(msg.id);
        if (msg.ok) {
          entry.resolve(msg.result);
        } else {
          entry.reject(new Error(msg.error));
        }
      });
      sharedWorker.addEventListener('error', () => {
        workerUnavailable = true;
        sharedWorker?.terminate();
        sharedWorker = null;
        for (const [, entry] of pending) {
          entry.reject(new Error('Layout worker failed'));
        }
        pending.clear();
      });
    } catch {
      workerUnavailable = true;
      return null;
    }
  }
  return sharedWorker;
}

export async function computeLayout2DAsync(input: Layout2DInput): Promise<Layout2DResult> {
  const worker = getLayoutWorker();
  if (!worker) {
    return computeLayout2DFallback(input);
  }
  const id = nextRequestId++;
  return new Promise<Layout2DResult>((resolve) => {
    pending.set(id, {
      resolve: (res) => resolve(res as Layout2DResult),
      reject: () => resolve(computeLayout2DFallback(input)),
    });
    try {
      worker.postMessage({ id, kind: '2d', input });
    } catch {
      pending.delete(id);
      resolve(computeLayout2DFallback(input));
    }
  });
}

export async function computeLayout3DAsync(input: Layout3DInput): Promise<Layout3DResult> {
  const worker = getLayoutWorker();
  if (!worker) {
    return computeLayout3DFallback(input);
  }
  const id = nextRequestId++;
  return new Promise<Layout3DResult>((resolve) => {
    pending.set(id, {
      resolve: (res) => resolve(res as Layout3DResult),
      reject: () => resolve(computeLayout3DFallback(input)),
    });
    try {
      worker.postMessage({ id, kind: '3d', input });
    } catch {
      pending.delete(id);
      resolve(computeLayout3DFallback(input));
    }
  });
}
