import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiTarget = process.env.VITE_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
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
      "/ws": {
        target: apiTarget,
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
