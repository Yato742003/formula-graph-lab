import { vi } from 'vitest';

class TestResizeObserver implements ResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

vi.stubGlobal('ResizeObserver', TestResizeObserver);

// jsdom has no top-layer dialog implementation; real focus trapping is checked in Chrome.
Object.assign(HTMLDialogElement.prototype, {
  showModal(this: HTMLDialogElement) { this.open = true; },
  close(this: HTMLDialogElement) { this.open = false; },
});

Object.defineProperty(globalThis, 'matchMedia', {
  configurable: true,
  value: vi.fn((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});
