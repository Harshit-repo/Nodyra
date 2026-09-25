import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../../../', import.meta.url));
export default defineConfig({
  testDir: '.',
  testMatch: '*.check.ts',
  workers: 1,
  timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:5187', viewport: { width: 1366, height: 900 } },
  webServer: {
    command: 'python -m http.server 5187 --bind 127.0.0.1 --directory brand/homepage',
    cwd: root,
    url: 'http://127.0.0.1:5187/nodyra.html',
    reuseExistingServer: !process.env.CI,
  },
});
