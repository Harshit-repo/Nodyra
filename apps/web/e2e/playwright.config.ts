import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));

// E2E stack runs on its own ports (API 8123, web 5191) so it never collides
// with a developer's running stack (8000/5173) or other local services.
//
// E2E_EXTERNAL_BASE_URL: run the same suite against an already-running stack
// (e.g. the docker-compose split topology: api dispatch_role=disabled + a
// dispatch worker) instead of booting servers here. The external stack must
// start from a fresh DB for the first-user setup test to pass.
const externalBaseURL = process.env.E2E_EXTERNAL_BASE_URL;
const productionBuild = process.env.E2E_PRODUCTION === "1";

export default defineConfig({
  testDir: ".",
  // *.e2e.ts so vitest's default include (*.{test,spec}.*) never picks these up.
  testMatch: "**/*.e2e.ts",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  use: {
    baseURL: externalBaseURL ?? "http://localhost:5191",
    trace: "retain-on-failure",
  },
  webServer: externalBaseURL ? undefined : [
    {
      command: "node start-api.mjs",
      cwd: here,
      url: "http://localhost:8123/health/live",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: productionBuild
        ? "npm run preview -- --port 5191 --strictPort"
        : "npm run dev -- --port 5191 --strictPort",
      cwd: path.resolve(here, ".."),
      url: "http://localhost:5191",
      reuseExistingServer: false,
      timeout: 120_000,
      env: { VITE_API_PROXY: "http://localhost:8123" },
    },
  ],
});
