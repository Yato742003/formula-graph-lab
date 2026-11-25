import {
  computeLayout2DFallback,
  computeLayout3DFallback,
  instantiateLayoutWasm,
  runWasmLayout2D,
  runWasmLayout3D,
  type FormulaLayoutWasmExports,
  type Layout2DInput,
  type Layout3DInput,
} from './graph-layout-wasm';

type WorkerRequest =
  | { id: number; kind: '2d'; input: Layout2DInput }
  | { id: number; kind: '3d'; input: Layout3DInput };

type WorkerScopeLike = {
  addEventListener: (type: 'message', listener: (event: MessageEvent<WorkerRequest>) => void) => void;
  postMessage: (message: unknown, transfer?: Transferable[]) => void;
};

const workerScope = globalThis as unknown as WorkerScopeLike;

let wasmExportsPromise: Promise<FormulaLayoutWasmExports | null> | null = null;

function loadWasmExports(): Promise<FormulaLayoutWasmExports | null> {
  if (!wasmExportsPromise) {
    wasmExportsPromise = fetch('/wasm/formula-layout.wasm')
      .then((response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return response.arrayBuffer();
      })
      .then((bytes) => instantiateLayoutWasm(bytes))
      .catch(() => null);
  }
  return wasmExportsPromise;
}

workerScope.addEventListener('message', async (event: MessageEvent<WorkerRequest>) => {
  const request = event.data;
  try {
    const exports = await loadWasmExports();
    if (request.kind === '2d') {
      const result = exports
        ? runWasmLayout2D(exports, request.input)
        : computeLayout2DFallback(request.input);
      workerScope.postMessage(
        { id: request.id, ok: true, kind: '2d', result },
        [result.nodeXY.buffer, result.groupRects.buffer],
      );
      return;
    }

    const result = exports
      ? runWasmLayout3D(exports, request.input)
      : computeLayout3DFallback(request.input);
    workerScope.postMessage(
      { id: request.id, ok: true, kind: '3d', result },
      [result.nodeXYZP.buffer],
    );
  } catch (err) {
    workerScope.postMessage({
      id: request.id,
      ok: false,
      error: err instanceof Error ? err.message : 'Unknown worker layout error',
    });
  }
});
