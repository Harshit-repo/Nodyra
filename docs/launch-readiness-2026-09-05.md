# Nodyra launch verification

Started 2026-09-05; updated 2026-09-11. This is the working release-candidate
record. **Public production launch is not yet certified.** Local acceptance has
exposed defects that were not caught by isolated unit tests; the fixes below
are being checked against the actual deployment and user journeys.

## What has been exercised

The acceptance deployment runs the production web image, FastAPI, a separate
dispatch worker, PostgreSQL, Redis, and S3-compatible MinIO. It uses synthetic
accounts and data. Its local address is `http://localhost:5188`; that address
does not establish public HTTPS or a production hosting account.

| Evidence | Result | Local evidence file |
| --- | --- | --- |
| Full Python suite, September 10, final source | 10,124 passed, 111 skipped, 7 warnings | `.tmp/launch-pytest-final-0910.log`, `.tmp/launch-pytest-final-0910.xml` |
| Full frontend suite with patched Vitest | 543 passed, 1 skipped, across 97 files | `.tmp/launch-vitest-patched-final-0909.log` |
| Production browser acceptance, September 10 | 11 passed in two runs: login, workflow creation/publication/execution, deployments, Community runner gating, file upload/download, workspace navigation, active-run contrast | `.tmp/launch-browser-infra-patched-0910.log`, `.tmp/launch-browser-core-infra-patched-0910.log` |
| Responsive/accessibility checks in that browser suite | 11 workspace pages at desktop and 390px mobile widths, light/dark themes, no serious/critical axe findings or horizontal page overflow, including a list containing over 100 workflows | `.tmp/launch-browser-current-0910.log` |
| PostgreSQL queue/runtime checks | 65 passed | `.tmp/launch-postgres.log` |
| Migrations | Fresh schema through revision 0096 and Alembic drift check passed | `.tmp/launch-migrations.log` |
| Recovery drill | Seven checks passed after restoring into a fresh database and bucket, including credential decryption with the restored organization key and artifact SHA-256 | `.tmp/launch-recovery-evidence.json` |
| Upload and persistence regression checks | 105 passed: real upload-to-run paths, simulated S3, organization isolation, deleted/corrupt uploads, artifact commit ordering, subworkflows | `.tmp/launch-upload-regression-0909.log` |
| Live quality gate | Clean metrics accepted, outlier metrics rejected, HTML profile generated | `.tmp/launch-quality-0909.json` |
| Helm render checks | 23 passed; non-root read-only web container also checked locally | `.tmp/launch-helm-final.log` |
| Dependency scans | npm audit and pip-audit reported no known vulnerabilities after the Vitest patch | `.tmp/launch-pip-audit-0909.json`, `.tmp/launch-npm-audit-fixed-0909.json` |
| Container scans | Final API/web, patched socket proxy, Redis, and patched quality sandbox pass the fixable high/critical gate. PostgreSQL retains gosu inventory findings; unfiltered API/runtime OS findings have a separate assessment. Bundled MinIO is excluded from the production recommendation | `.tmp/launch-trivy-api-final-0910.log`, `.tmp/launch-trivy-web-final-0910.log`, [assessment](security/launch-image-assessment.md) |
| Image identity and inventory | API and worker have identical filesystem layers; current API SBOM has the same 210 package names/versions as the assessed image; fresh API/web SBOMs retained | `.tmp/launch-candidate-image-ids-0910.txt`, `.tmp/launch-image-equivalence-0910.json`, `.tmp/launch-sbom-package-delta-0910.json`, `.tmp/launch-api-sbom-current-0910.spdx.json`, `.tmp/launch-web-sbom-current-0910.spdx.json` |
| Required sandbox integration | Two real Docker checks passed, including uploaded CSV → DatasetRef → exported CSV, non-root/read-only execution, warm reuse for pure code, and recycling file-bearing containers | `.tmp/launch-sandbox-real-0909.log` |
| Repeated local live acceptance | Five consecutive runs of all 18 scenarios passed on September 10, before the additional infrastructure fixes; the changed images are being requalified | `.tmp/launch-repeat-1-0910.json` through `.tmp/launch-repeat-5-0910.json` |
| API address replacement | Web recovered after an actual backend IP change in 10.58 seconds without restarting; Helm uses a namespace/cluster-qualified service address | `.tmp/launch-dns-replacement-0910.json` |
| External S3 deployment configuration | Renders with MinIO excluded and external settings propagated to API/worker; actual AWS credentials and bucket remain unqualified | `.tmp/launch-external-s3-render-0910.json` |
| Engine planning budgets | All four benchmarks passed: wide/deep/fan-in-out 10k-node graphs and nested-loop indexing | `.tmp/launch-planning-benchmark-0909.json` |
| Post-hardening regression checks | 124 targeted API/core/node tests passed, 68 client/runtime/runner tests passed, and Ruff passed through the workspace `uv` environment | terminal run on 2026-09-11 |
| Isolated release browser suite | 11 of 11 Playwright journeys passed in 2.3 minutes, including owner setup, workflow creation and execution, credentials, publishing, deployments, runner pools, responsive pages, and operational navigation | `apps/web/e2e` via `npm run test:e2e` |

These files are private local test outputs, not published release attachments.
The recovery fixture is deliberately small; its observed restore time is not
a production RTO promise. Helm rendering does not prove operation in a real
Kubernetes cluster. The current images were scanned after their last build;
any later rebuild requires new image evidence.

## Defects addressed during acceptance

- Authentication state could race sign-out; stale auth work is now discarded.
- Production nginx configuration, upload limits, caching, and Helm writable
  paths were corrected and checked with the built web image.
- Several template graphs had incompatible node inputs or enum values. The
  generator and shipped templates now agree, with parameter validation guarding
  against a repeat. HTTP health checks can inspect status and headers explicitly.
- Run success and completion events could become visible before artifact
  metadata was durable. Artifact persistence is now part of the lease-fenced
  outcome transaction, and terminal notification follows that commit.
- Workflow deadlines now produce `timed_out`; error handlers also handle that
  terminal status. Failure/retry/cancellation scenarios were exercised live.
- HTTP redirects strip credentials when the destination origin changes.
  Compose explicitly blocks private and metadata-address egress by default.
- Browser-upload readers used an obsolete path and could not retrieve S3
  uploads. The host now authorizes each selected file, verifies its size and
  checksum, and stages it under its organization and run. Deleted uploads
  cannot silently reuse old staged bytes.
- The profiling library's `pkg_resources` import fails with recent setuptools.
  The node and template declare a compatible optional environment requirement.
- CSV exports neutralize spreadsheet formula injection. Artifact upload remains
  available in populated lists; narrow screens retain access to file details.
- Sandbox file transport, cached S3 dataset rehydration, cross-environment
  subworkflow isolation, and runtime-image cache invalidation were verified
  with real Docker runs and live application workflows. The read-only
  filesystem and non-root runtime remain enabled.
- SQLite cancellation now reserves its write lock before reading run state,
  avoiding a WAL snapshot race with the cancelled worker's outcome commit.
- Running status badges retain contrast in both themes. Pagination wraps on
  phones when larger workflow lists need more page buttons.
- Compose API, worker, and web deny privilege gain. API/worker PID 1 run as
  UID/GID 10001 with zero effective capabilities and `NoNewPrivs: 1`.
- Web nginx refreshes upstream DNS after API address changes. The real
  replacement test recovered without restarting the web container.
- The stuck-run detector ignores unfinished timestamps when finding the latest
  completed node, and only reports failure for runs its conditional update
  actually changed. A concurrent successful completion cannot emit a false
  failure event.

## Realistic workflow coverage

`scripts/live_workflow_acceptance.py` creates inspectable workflows named
`QA · …`, runs them through authenticated HTTP endpoints, checks their results,
and records workflow/run IDs in JSON. It covers customer CSV cleaning and
deduplication, conditional routing, text extraction, HTTP health, authenticated
webhook validation, intentional failure and recovery, cancellation, deadlines,
subworkflows, private-egress rejection, mixed-success HTTP batches, and API-to-CSV
export. Additional cases build a Python environment, exercise quality gates,
export dated Excel snapshots, wait for a real scheduled run, retry a persisted
S3 dataset, pass datasets between sandbox environments, and read non-finite
analytical results through the API.

Use disposable acceptance data and an owner account:

```powershell
# Set NODYRA_TEST_EMAIL and NODYRA_TEST_PASSWORD privately in the environment.
uv run python scripts/live_workflow_acceptance.py --report .tmp/live-acceptance.json
uv run python scripts/live_workflow_acceptance.py `
  --cases environment,quality,snapshot,scheduler `
  --report .tmp/live-data-acceptance.json
```

Five complete local live passes also succeeded on September 10. The September 11
isolated release browser suite passed all 11 journeys after the runtime-pool,
output-store, environment-build, MCP, and homepage hardening changes. The root
directory `python -m pytest -q` command is intentionally not treated as release
evidence because it bypasses the workspace package installation and cannot
import `nodyra_client`/`nodyra_runtime`; use `uv run pytest` for workspace
packages and the API test command used for the full suite.

The September 10
requalification found that the test environment needed an explicit pandas
declaration after the optional-dependency preflight was corrected. The fixture
and environment were updated accordingly. Subsequent infrastructure scanning
found patchable sandbox/proxy/database dependencies and an unsupported bundled
MinIO image. Updates and an external-S3 production overlay were added; those
changed images are undergoing live requalification. No external email, Slack
message, payment, or other customer-facing
integration action was performed with real credentials.

## Remaining release gates

1. Finish requalification of the infrastructure fixes and bind the final
   results to the current worktree and image identities.
2. Qualify the public host: real domain, HTTPS, exact trusted-proxy topology,
   required sandbox runtime, and live ingress tests. The current local
   attestation has one failure (`edge.tls_proxy`) and three
   external-evidence warnings (backup, external key manager, tracing).
3. Attach production backup/restore, key-management, and observability evidence.
   Local MinIO on the application machine is not an off-host backup.
4. Run release CI on the chosen source commit and attach its SBOMs, immutable
   digests, signatures, provenance, scans, upgrade evidence, and rollback notes.
   Nothing has been pushed or publicly released by this verification work.
5. Complete the RC policy's certification on the approved deployment and
   identify the support/security-response owner for GA. The five local passes
   are supporting evidence, not public-host certification. Review the
   [remaining image findings](security/launch-image-assessment.md). See
   [release policy](release-policy.md) and [release evidence](operations/release-evidence.md).

The suggested initial hosting shape and its constraints are documented in
[the launch hosting runbook](deployment/launch-hosting.md). Vendor integrations
need their own credential-backed acceptance before they are advertised as
qualified. Enterprise multi-tenant and remote-runner deployments need matching
licensed infrastructure tests; Community browser gating alone is not evidence
for those execution modes.
