// E2E API launcher: resets the throwaway SQLite DB, runs migrations, then
// starts uvicorn on the e2e port (8123) so the suite never collides with a
// running dev stack (8000) or other local services.
// REDIS_URL="" forces the in-process event broker (no Redis needed for e2e).
import { spawn, spawnSync } from "node:child_process";
import { rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const apiDir = path.resolve(here, "..", "..", "api");
const repoRoot = path.resolve(apiDir, "..", "..");

for (const name of ["e2e.db", "e2e.db-wal", "e2e.db-shm"]) {
  rmSync(path.join(apiDir, name), { force: true });
}

const python =
  process.platform === "win32"
    ? path.join(repoRoot, ".venv", "Scripts", "python.exe")
    : path.join(repoRoot, ".venv", "bin", "python");

const env = {
  ...process.env,
  DATABASE_URL: "sqlite+aiosqlite:///./e2e.db",
  // Exercise the production auth path: first-user registration + sessions.
  AUTH_REQUIRED: "true",
  // Use a non-default key so the API's production security checks remain
  // enabled while this isolated, throwaway stack starts successfully.
  SECRET_KEY: "nodyra-e2e-only-secret-key-2026-do-not-use-in-production",
  // Closed port: the broker's startup ping fails and it falls back to the
  // in-process transport, so e2e needs no Redis. ("" would crash the eager
  // redis.from_url parse in app/redis_client.py.)
  REDIS_URL: "redis://localhost:6390/0",
};

// Migrate-then-start: the API does not create its own schema.
const migrate = spawnSync(python, ["-m", "alembic", "upgrade", "head"], {
  cwd: apiDir,
  stdio: "inherit",
  env,
});
if (migrate.status !== 0) {
  console.error("alembic upgrade head failed");
  process.exit(migrate.status ?? 1);
}

const child = spawn(
  python,
  ["-m", "uvicorn", "app.main:app", "--port", "8123"],
  { cwd: apiDir, stdio: "inherit", env },
);
child.on("exit", (code) => process.exit(code ?? 0));
