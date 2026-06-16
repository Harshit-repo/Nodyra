# 01 — Infrastructure & Deployment

Independent review, 2026-06-16. Files: `deploy/Dockerfile.python`,
`deploy/docker-compose.yml`, `.dockerignore`, `.gitignore`,
`.github/workflows/ci.yml`.

## INFRA-1 — `uv.lock` is gitignored → `uv sync --locked` breaks on a clean clone (HIGH)
`.gitignore:14` lists `uv.lock`, so the lockfile is **never committed**. But
`deploy/Dockerfile.python:14` runs `uv sync --locked --no-dev --all-packages`,
and the README documents `docker compose -f deploy/docker-compose.yml up --build`
as the install path.
- **Evidence:** `git ls-files --error-unmatch uv.lock` → *"did not match any
  file(s) known to git"*. `.dockerignore` does **not** exclude `uv.lock`, so the
  maintainer's local build works only because the file exists untracked in the
  working tree. CI uses `uv sync --all-packages` (no `--locked`, line 16/63) so it
  silently regenerates the lock and never surfaces the break.
- **Why it matters:** a fresh `git clone` (OSS contributor, production CD, a clean
  CI Docker build) has no `uv.lock`; `uv sync --locked` then fails. Builds are
  also **non-reproducible** — every environment resolves dependencies afresh,
  which is exactly what a lockfile is meant to prevent and is a supply-chain risk.
  uv's own guidance is to commit application lockfiles.
- **Fix:** remove `uv.lock` from `.gitignore`, commit it, and add a CI lane that
  runs `uv sync --locked` (or `uv lock --check`) so drift fails the build.
- **Status:** Needs fix (safe, high value). *Blocks open-source + production.*

## INFRA-2 — Containers run as root (MEDIUM)
`Dockerfile.python` has no `USER` directive; API and worker run as **root**
inside the container. (Note: the *sandbox-per-run* containers correctly drop all
caps — this finding is about the API/worker image itself.)
- **Impact:** larger blast radius if the API/worker is compromised; fails common
  container-security baselines (CIS, k8s `runAsNonRoot`). The Helm chart should be
  checked for a `securityContext` too.
- **Fix:** add a non-root `USER`, `chown` `/app/.venv`, `/app/envs`, `/app/artifacts`;
  set `runAsNonRoot: true` in the Helm `securityContext`.
- **Status:** Reviewed.

## INFRA-3 — Dockerfile defeats layer caching (MEDIUM, build perf)
`COPY . .` (line 12) precedes `uv sync` (line 14), so **any** source change
invalidates the dependency layer and reinstalls the full dependency tree every
build.
- **Fix:** copy `pyproject.toml`, all workspace `*/pyproject.toml`, and `uv.lock`
  first; `uv sync --locked --no-install-project`; then `COPY . .` and a final
  `uv sync`. Pair with INFRA-1.
- **Status:** Reviewed.

## INFRA-4 — `.dockerignore` may ship built per-env venvs into the image (MEDIUM — verify)
`.dockerignore` excludes `envs/` (root-relative) but `COPY . .` will still pull
`apps/api/envs/<id>/` (the uv-built per-environment venvs seen in the working
tree) because dockerignore patterns are matched from the context root.
- **Impact:** image bloat (each env venv is tens–hundreds of MB) and possible
  leakage of env-specific installed packages/state into the shared image.
- **Fix:** add `**/envs/` and `apps/api/envs/` to `.dockerignore`; confirm with
  `docker build` + `docker history`/size.
- **Status:** Needs verification.

## INFRA-5 — No healthcheck for `api`/`worker` services (LOW–MEDIUM)
`docker-compose.yml` defines healthchecks for postgres/redis/(minio implicit) but
not for `api` or `worker`. `depends_on` only waits for postgres/redis health, and
nothing gates on the API actually serving.
- **Fix:** add a compose healthcheck hitting `routers/health.py` (e.g.
  `curl -f http://localhost:8000/health`); add a worker liveness probe. Mirror in
  Helm (`livenessProbe`/`readinessProbe`).
- **Status:** Reviewed.

## Positives
- Sensible service split (postgres/redis/minio/api/worker), named volumes for
  pg/envs/artifacts (persistence), `INTERNAL_API_TOKEN` required (fails closed),
  `AUTH_REQUIRED` defaults true, `SECRET_KEY` overridable. Single shared image for
  api+worker is a reasonable simplification. Helm chart present for k8s.
- `DISPATCH_ROLE`/`SCHEDULER_ROLE` give a clean control/worker split.

## CI notes (`.github/workflows/ci.yml`)
- Runs ruff, pytest (sqlite + a real-Postgres lane with `alembic upgrade head` +
  `alembic check`), web typecheck/build, and Playwright e2e. Good coverage shape.
- **Gap:** no `uv sync --locked` lane (see INFRA-1) and no Docker image build/
  smoke job — so the broken `--locked` path is invisible to CI. Add both.
