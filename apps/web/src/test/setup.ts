import "@testing-library/jest-dom/vitest";

// Polyfill ResizeObserver for @xyflow/react (not implemented in jsdom)
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
