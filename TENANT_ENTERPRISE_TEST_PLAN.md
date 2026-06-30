# Noodle Tenant and Enterprise Test Plan

Date: 2026-06-30
Status: ✅ All tests passing — 76 files, 439 passed, 1 skipped. TypeScript clean.
Branch: `feat/ms4-enterprise-scale`

## Scope

This plan covers Noodle across three operating modes:

- Single-tenant/self-hosted: one trusted deployment, local or self-managed, with SQLite or Postgres and reduced enterprise requirements.
- Multi-tenant SaaS: multiple organizations on one control plane, tenant isolation enforced by application scoping and Postgres RLS.
- Enterprise: multi-tenant plus RBAC, audit, SSO, credential protection, operational readiness, and scalable worker/queue behavior.

## Architecture Map

### Entrypoints

- Backend API: `apps/api/app/main.py`
- API config and production hardening: `apps/api/app/config.py`
- Worker entrypoint: `apps/api/app/worker_main.py`
- Frontend app: `apps/web/src`
- Node/runtime packages: `packages/core`, `packages/nodes`, `packages/runner`, `packages/runtime`
- Deployment: `deploy/docker-compose.yml`, `deploy/Dockerfile.python`, `apps/web/Dockerfile`, `deploy/helm/noodle`

### Tenant, Org, User, and Data Models

- Core models: `apps/api/app/models.py`
- Tenant context and RLS safety: `apps/api/app/tenancy.py`, `apps/api/app/main.py`
- Organizations/memberships/settings: `Organization`, `Membership`, `OrgSettings`
- Workflows and execution: `Workflow`, `WorkflowVersion`, `Run`, `NodeRun`, `RunEvent`, `RunApproval`
- Tenant data surfaces: workflows, folders, credentials, environments, runner pools, runs, artifacts, code modules, deployments, MCP connections, SSO configs, GitHub sync configs, audit events, pinned data, run queue entries

### Auth, RBAC, and Enterprise

- Auth routes: `apps/api/app/routers/auth.py`
- RBAC helpers: `apps/api/app/security.py`
- Admin/enterprise routes: `apps/api/app/routers/admin.py`
- SSO service: `apps/api/app/services/sso.py`
- License and feature gates: `apps/api/app/services/licensing.py`
- Audit routes/events: `apps/api/app/routers/audit.py`, `apps/api/app/routers/admin.py`

### Credentials and Secrets

- Credential routes: `apps/api/app/routers/credentials.py`
- Credential storage and resolution: `apps/api/app/services/credentials.py`
- Encryption and hashing: `apps/api/app/services/crypto.py`
- Org KEK support: `apps/api/app/services/org_keys.py`
- Redaction helpers: `apps/api/app/services/redaction.py`
- External KMS providers: `apps/api/app/services/kms`

### Webhooks

- Webhook dispatch: `apps/api/app/routers/webhooks.py`
- Provider webhooks: `apps/api/app/routers/provider_webhooks.py`
- GitHub sync webhook/config: `apps/api/app/routers/github_sync.py`, `apps/api/app/services/github_sync.py`

### MCP and AI

- MCP server/tools: `apps/api/app/routers/mcp.py`, `apps/api/app/mcp/tools.py`
- MCP client/connections: `apps/api/app/routers/mcp_connections.py`, `apps/api/app/services/mcp_client.py`
- AI builder/routes: `apps/api/app/routers/agentic_build.py`, `apps/api/app/services/ai_builder.py`, `apps/api/app/services/agentic_builder.py`
- Chat/agent routes: `apps/api/app/routers/chat.py`, `apps/api/app/services/chat_service.py`

### Queue, Worker, Runtime, and Artifacts

- Queue service: `apps/api/app/services/queue.py`
- Runner pools: `apps/api/app/routers/runner_pools.py`
- Runtime pool: `apps/api/app/services/runtime_pool.py`
- Artifacts: `apps/api/app/routers/artifacts.py`, `apps/api/app/services/artifacts.py`, `apps/api/app/services/artifact_backends.py`, `apps/api/app/services/s3_artifact_backend.py`

### Database and Migrations

- Database session: `apps/api/app/db.py`
- Alembic migrations: `apps/api/alembic/versions`
- RLS hardening: `apps/api/alembic/versions/0058_tenant_rls_hardening.py` plus later tenant tables
- Postgres application role setup: `deploy/postgres-role-setup.sql`

### Frontend Tenant UX

- API client and org header: `apps/web/src/api.ts`
- Org switcher: `apps/web/src/HomeHeader.tsx`
- Org administration page: `apps/web/src/OrganizationPage.tsx`
- Permission model/tests: `apps/web/src/permissions.ts`, `apps/web/src/permissions.test.tsx`

## Assumptions

### Single-Tenant

- SQLite/local operation is acceptable for trusted self-hosted use.
- Default org behavior should not require enterprise setup.
- Arbitrary Python/code-node execution can be acceptable only when the operator trusts users and host access.
- Docker Compose should run API, worker, web, Postgres, Redis, and object storage with documented required secrets.

### Multi-Tenant

- Postgres is required for strong isolation; SQLite is not treated as an enterprise tenant boundary.
- `X-Org-Id` and membership resolution establish the active organization.
- Tenant context must be reset per request and must not leak across pooled connections.
- All tenant tables must have org scoping, and Postgres RLS must scope correctly when tenant context is set. Any fail-open maintenance path for unset tenant context must be proven unreachable from tenant request/worker paths.
- Arbitrary code execution must be sandboxed or disabled/restricted for untrusted tenants.

### Enterprise

- Enterprise readiness requires RBAC, SSO, audit logs, secret protection, health checks, operational controls, and safe deployment defaults.
- Admin and instance-global routes must not be reachable through workspace-level permissions.
- SSO users must be created safely with valid local model invariants and valid membership roles.
- Helm and Docker artifacts must be validated before a customer pilot.

## Components to Test

- Auth registration, login, session cookies, bearer tokens, API tokens, logout, revoked sessions
- Org creation, switching, membership, role changes, current-org resolution
- RBAC for owner/admin/editor/viewer and instance-global permissions
- Workflow CRUD, versions, import/export, deployment, folders, pinned outputs
- Runs, node runs, run events, approvals, run history, cancel/replay/debug snapshots
- Credentials, OAuth callbacks, credential tests, credential masking and org scoping
- Webhooks, test webhooks, provider webhooks, auth schemes, duplicate paths, large payload behavior
- MCP server, MCP client, MCP connections, MCP workflow tools, external URL handling
- AI builder, chat, agent tool calls, secret redaction, unsafe-node approvals
- Python code nodes, package access, filesystem/network/env access, timeouts, memory behavior
- Queue, workers, leases, dead-letter behavior, runner pools, artifact upload/download
- Audit logs, admin surfaces, KMS health, SSO configuration, license gates
- Frontend org switching, permission-gated controls, stale cache behavior, error handling
- Docker Compose, Docker images, Helm chart, migration job, health/readiness/liveness probes

## Missing Test Areas

- End-to-end browser tests that switch users/orgs and validate cache clearing.
- Full route-by-route IDOR matrix for every ID-bearing route.
- High-concurrency webhook and queue stress tests with real Redis/Postgres/object storage.
- Large workflow canvas performance tests with 100+ nodes and high-frequency run events.
- Production Docker image build and startup smoke on clean machines.
- Helm render/lint/deploy validation in a Kubernetes test namespace.
- Dedicated sandbox escape tests for Python code nodes in multi-tenant mode.
- SSRF tests for HTTP nodes, MCP connection URLs, webhooks, and OAuth callback flows.
- Brute-force/rate-limit tests for public auth and webhook endpoints.
- Audit log immutability/tamper-resistance tests.
- Backup/restore and migration-from-existing-version tests.

## Test Strategy

### Baseline

1. Run all Python tests with timeouts enabled.
2. Run focused API suites for org RBAC, tenancy scoping, Postgres RLS, SSO, MCP, credentials, webhooks, AI builder, runs, and worker/queue behavior.
3. Run frontend unit tests and a production frontend build.
4. Validate Docker Compose syntax using the example environment.
5. Inspect Helm chart and run `helm lint`/`helm template` where Helm is available.

### Single-Tenant

- Start with default local settings and verify registration, default org, workflow lifecycle, run execution, credentials, logs, artifacts, webhooks, MCP, and frontend navigation.
- Confirm single-tenant mode does not require Postgres RLS, SSO, enterprise license, or external KMS.
- Confirm unsafe defaults are allowed only for local/trusted use and are warned or rejected when production boundaries are enabled.

### Multi-Tenant

- Create Org A, Org B, and multiple users with different memberships.
- Exercise each list/read/update/delete/run route with Org A IDs while authenticated as Org B.
- Verify app-level scoping and database-level RLS both deny cross-tenant reads and writes.
- Verify queue, worker, artifacts, webhooks, MCP, AI builder, and background jobs preserve org context.

### Enterprise

- Enable multi-tenancy and enterprise features.
- Test owner/admin/editor/viewer permission boundaries.
- Test SSO/OIDC/SAML JIT provisioning and disabled provisioning.
- Test audit log access, custom roles, license/feature gates, KMS health, admin settings, and secret handling.
- Validate production config rejects default secrets, wildcard CORS with credentials, and RLS-bypass Postgres roles.

## Security Test Strategy

- Run danger-pattern review for raw `session.get`, unscoped `select`, `filter_by(id=...)`, ID routes, missing `Depends(require_permission)`, and cross-tenant background workers.
- Search for secret exposure in logs, API serializers, frontend console output, AI prompts, MCP tool payloads, webhook URLs, and test fixtures.
- Validate CSRF behavior for cookie-auth state-changing requests and bearer-token exemptions.
- Test CORS production hardening and insecure default rejection.
- Test webhook authentication, duplicate path behavior, inactive workflow behavior, and secret redaction.
- Test MCP URL handling, auth failures, tenant-scoped tool listing, and side-effecting tool permissions.
- Treat unrestricted Python execution in untrusted multi-tenant deployments as a release blocker unless a real sandbox boundary is enforced.

## Stress and Performance Strategy

- Backend API: benchmark workflow list, run list, run detail, timeline, credentials list, audit log, and org switching.
- Queue/worker: run 10 concurrent runs in one org, 10 across two orgs, 100 queued runs, worker restart during execution, cancel during execution, retry after failure.
- Webhooks: run 50 concurrent calls per active webhook path, duplicate path checks across orgs, large payload and malformed payload tests.
- Database: capture slow query logs and explain plans for tenant-scoped list endpoints, run history, queue polling, audit logs, and workflow save.
- Frontend: measure bundle size, large canvas render time, timeline render time, React Query cache isolation, WebSocket event flooding, and memory after org switching.
- Deployment: measure cold start time, health check latency, worker readiness, image size, and migration duration on a fresh Postgres database.

## Regression Gate

Before enterprise pilot:

- Full Python suite passes.
- Frontend unit tests pass.
- Frontend production build passes.
- Postgres migration from empty database to head passes.
- Postgres RLS isolation suite passes.
- Docker Compose config validation passes and a smoke environment starts.
- Helm chart renders and passes lint.
- New route-by-route tenant isolation tests cover all sensitive ID-bearing routes.
- No open P0 issues and no open P1 issues in auth, tenancy, RBAC, credentials, webhook, MCP, AI, database, queue, or deployment areas.
