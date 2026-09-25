import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiTarget = process.env.VITE_API_PROXY ?? "http://localhost:8000";

function manualChunks(id: string): string | undefined {
  const normalized = id.replaceAll("\\", "/");
  // This helper is shared by eager routes and lazy imports. If it is folded
  // into Monaco, the router imports the whole editor during initial startup.
  if (normalized === "\0vite/preload-helper.js") return "preload-helper";
  if (!normalized.includes("/node_modules/")) return undefined;
  if (
    normalized.includes("/node_modules/react/") ||
    normalized.includes("/node_modules/react-dom/")
  ) {
    return "vendor-react";
  }
  if (
    normalized.includes("/node_modules/react-router") ||
    normalized.includes("/node_modules/@remix-run/router")
  ) {
    return "vendor-router";
  }
  if (normalized.includes("/node_modules/@tanstack/react-query/")) {
    return "vendor-query";
  }
  if (normalized.includes("/node_modules/@xyflow/")) {
    return "vendor-xyflow";
  }
  if (
    normalized.includes("/node_modules/@monaco-editor/") ||
    normalized.includes("/node_modules/monaco-editor/")
  ) {
    return "vendor-monaco";
  }
  if (normalized.includes("/node_modules/plotly.js-basic-dist-min/")) {
    return "vendor-plotly";
  }
  if (
    normalized.includes("/node_modules/@phosphor-icons/") ||
    normalized.includes("/node_modules/lucide-react/") ||
    normalized.includes("/node_modules/simple-icons/")
  ) {
    return "vendor-icons";
  }
  if (
    normalized.includes("/node_modules/marked/") ||
    normalized.includes("/node_modules/dompurify/")
  ) {
    return "vendor-markdown";
  }
  if (normalized.includes("/node_modules/zustand/")) {
    return "vendor-state";
  }
  return undefined;
}

export default defineConfig({
  plugins: [react()],
  build: {
    // Plotly's basic distribution is a deliberately lazy, self-contained chunk.
    // Keep the warning useful for eager code by splitting vendors explicitly and
    // setting the threshold just above that known on-demand asset.
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks,
      },
    },
  },
  server: {
    host: true,
    port: 5173,
    // Bind-mounted source in a container on Windows/macOS often doesn't receive
    // native FS change events, so Vite silently misses edits and HMR serves
    // stale modules. Set VITE_USE_POLLING=1 in the dev container to fall back to
    // polling. Left off by default so native dev keeps cheap event-based watching.
    watch: process.env.VITE_USE_POLLING
      ? { usePolling: true, interval: 300 }
      : undefined,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/mcp": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/.well-known/oauth-protected-resource/mcp": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/ws": {
        target: apiTarget,
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
