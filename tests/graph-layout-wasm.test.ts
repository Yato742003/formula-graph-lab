import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeAll, describe, expect, it } from 'vitest';
import {
  computeLayout2DFallback,
  computeLayout3DFallback,
  instantiateLayoutWasm,
  runWasmLayout2D,
  runWasmLayout3D,
  type FormulaLayoutWasmExports,
  type Layout2DInput,
  type Layout3DInput,
} from '../lib/graph-layout-wasm';

let wasmExports: FormulaLayoutWasmExports;

beforeAll(async () => {
  const wasmPath = resolve(process.cwd(), 'public/wasm/formula-layout.wasm');
  const bytes = readFileSync(wasmPath);
  wasmExports = await instantiateLayoutWasm(bytes);
});

describe('Rust WASM layout engine (public/wasm/formula-layout.wasm)', () => {
  it('computes 2D variable-height lane layouts with zero card overlap and matches TS fallback invariants', () => {
    const input: Layout2DInput = {
      nodeGroupIds: new Uint32Array([0, 1, 1, 1, 2, 2, 3, 3]),
      nodeKindRanks: new Uint32Array([0, 2, 2, 2, 2, 2, 5, 5]),
      nodeWidths: new Float32Array([280, 320, 320, 320, 320, 320, 320, 320]),
      nodeHeights: new Float32Array([148, 138, 256, 192, 144, 224, 165, 210]),
      edgeSrcs: new Uint32Array([0, 1, 2, 4]),
      edgeDsts: new Uint32Array([1, 4, 6, 7]),
      edgeWeights: new Float32Array([1, 2, 2, 2]),
      groupCount: 4,
      groupMaxCols: new Uint32Array([1, 1, 1, 1]),
      groupBaryFlags: new Uint32Array([0, 0, 0, 1]),
      params: {
        lanePadX: 24,
        lanePadTop: 72,
        lanePadBottom: 24,
        cardGapX: 24,
        cardGapY: 20,
        laneGapX: 56,
        laneGapY: 44,
        maxLanesPerRow: 3,
      },
    };

    const wasmResult = runWasmLayout2D(wasmExports, input);
    const tsResult = computeLayout2DFallback(input);

    expect(wasmResult.engine).toBe('rust-wasm');
    expect(wasmResult.nodeXY.length).toBe(16);
    expect(wasmResult.groupRects.length).toBe(16);

    for (const result of [wasmResult, tsResult]) {
      const n = input.nodeGroupIds.length;
      for (let i = 0; i < n; i++) {
        const xi = result.nodeXY[i * 2];
        const yi = result.nodeXY[i * 2 + 1];
        const wi = input.nodeWidths[i];
        const hi = input.nodeHeights[i];
        for (let j = i + 1; j < n; j++) {
          const xj = result.nodeXY[j * 2];
          const yj = result.nodeXY[j * 2 + 1];
          const wj = input.nodeWidths[j];
          const hj = input.nodeHeights[j];
          const overlapX = xi < xj + wj && xi + wi > xj;
          const overlapY = yi < yj + hj && yi + hi > yj;
          expect(overlapX && overlapY).toBe(false);
        }

        // Card must remain strictly inside its owning lane rectangle.
        const g = input.nodeGroupIds[i];
        const gx = result.groupRects[g * 4];
        const gy = result.groupRects[g * 4 + 1];
        const gw = result.groupRects[g * 4 + 2];
        const gh = result.groupRects[g * 4 + 3];
        expect(xi).toBeGreaterThanOrEqual(gx - 0.1);
        expect(xi + wi).toBeLessThanOrEqual(gx + gw + 0.1);
        expect(yi).toBeGreaterThanOrEqual(gy - 0.1);
        expect(yi + hi).toBeLessThanOrEqual(gy + gh + 0.1);
      }
    }

    // Rust WASM and TS mirror must agree closely on geometry.
    for (let i = 0; i < wasmResult.nodeXY.length; i++) {
      expect(Math.abs(wasmResult.nodeXY[i] - tsResult.nodeXY[i])).toBeLessThan(0.5);
    }
  });

  it('folds large section lanes into multiple balanced sub-columns in WASM', () => {
    const count = 25;
    const nodeGroupIds = new Uint32Array(count);
    const nodeKindRanks = new Uint32Array(count);
    const nodeWidths = new Float32Array(count);
    const nodeHeights = new Float32Array(count);
    nodeGroupIds[0] = 0;
    nodeKindRanks[0] = 0;
    nodeWidths[0] = 280;
    nodeHeights[0] = 148;
    for (let i = 1; i < count; i++) {
      nodeGroupIds[i] = 1;
      nodeKindRanks[i] = 1;
      nodeWidths[i] = 220;
      nodeHeights[i] = 48;
    }

    const res = runWasmLayout2D(wasmExports, {
      nodeGroupIds,
      nodeKindRanks,
      nodeWidths,
      nodeHeights,
      edgeSrcs: new Uint32Array(0),
      edgeDsts: new Uint32Array(0),
      edgeWeights: new Float32Array(0),
      groupCount: 2,
      groupMaxCols: new Uint32Array([1, 3]),
      groupBaryFlags: new Uint32Array([0, 0]),
      params: {
        lanePadX: 24,
        lanePadTop: 72,
        lanePadBottom: 24,
        cardGapX: 20,
        cardGapY: 14,
        laneGapX: 56,
        laneGapY: 44,
        maxLanesPerRow: 3,
      },
    });

    const laneHeight = res.groupRects[7];
    expect(laneHeight).toBeGreaterThan(250);
    expect(laneHeight).toBeLessThan(750);
  });

  it('computes 3D hierarchical constellation with overlap separation and epistemic Y halo', () => {
    // 1 Paper, 3 Sections, 9 Equations, 9 Symbols, 2 Hypotheses
    const kinds: number[] = [0];
    const parents: number[] = [-1];
    const roots: number[] = [0];

    for (let s = 0; s < 3; s++) {
      kinds.push(1);
      parents.push(0);
      roots.push(0);
    }
    for (let eq = 0; eq < 9; eq++) {
      kinds.push(2);
      parents.push(1 + (eq % 3));
      roots.push(0);
    }
    for (let sym = 0; sym < 9; sym++) {
      kinds.push(3);
      parents.push(4 + sym);
      roots.push(0);
    }
    for (let h = 0; h < 2; h++) {
      kinds.push(5);
      parents.push(0);
      roots.push(0);
    }

    const input: Layout3DInput = {
      nodeKinds: new Uint32Array(kinds),
      nodeParents: new Int32Array(parents),
      nodeRootPapers: new Int32Array(roots),
      edgeSrcs: new Uint32Array([4, 5]),
      edgeDsts: new Uint32Array([5, 6]),
      edgeKindCodes: new Uint32Array([1, 1]),
    };

    const wasmRes = runWasmLayout3D(wasmExports, input);
    const tsRes = computeLayout3DFallback(input);

    expect(wasmRes.engine).toBe('rust-wasm');
    expect(wasmRes.nodeXYZP.length).toBe(kinds.length * 4);

    for (const res of [wasmRes, tsRes]) {
      const n = kinds.length;
      for (let i = 0; i < n; i++) {
        const ix = res.nodeXYZP[i * 4];
        const iy = res.nodeXYZP[i * 4 + 1];
        const iz = res.nodeXYZP[i * 4 + 2];
        const ip = res.nodeXYZP[i * 4 + 3];
        expect(Number.isFinite(ix)).toBe(true);
        expect(Number.isFinite(iy)).toBe(true);
        expect(Number.isFinite(iz)).toBe(true);
        expect(ip).toBeGreaterThanOrEqual(0.02);
        expect(ip).toBeLessThanOrEqual(1);

        for (let j = i + 1; j < n; j++) {
          const d = Math.hypot(
            ix - res.nodeXYZP[j * 4],
            iy - res.nodeXYZP[j * 4 + 1],
            iz - res.nodeXYZP[j * 4 + 2],
          );
          expect(d).toBeGreaterThan(1.2);
        }
      }

      // Epistemic separation: Hypotheses sit clearly above the Paper evidence plane.
      const paperY = res.nodeXYZP[1];
      for (let i = 0; i < n; i++) {
        if (kinds[i] === 5) {
          expect(res.nodeXYZP[i * 4 + 1]).toBeGreaterThan(paperY + 3.0);
        }
      }
    }
  });
});
