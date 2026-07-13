import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    css: true,
    // Each jsdom worker loads the complete editor dependency graph. Capping
    // process fan-out avoids memory/CPU thrash on high-core CI hosts while
    // retaining parallel test-file isolation.
    maxWorkers: 4,
  },
});
