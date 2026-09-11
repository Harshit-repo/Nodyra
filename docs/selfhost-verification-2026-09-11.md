# Self-hosted installation verification — 11 September 2026

## Result and scope

The source-built, trusted-team installation passed the fresh-install and local
HTTP/HTTPS ingress acceptance checks below. This does **not** certify an
anonymous download or published binary release: distribution is still blocked.

Application source tested: `f9398be660ac5abcf8406340128540288adfce0b`.
The initial export was `f2dc4ac9`; the Windows startup failure found in that
export was corrected in `f9398be6` and the source was exported again before
rebuilding. Later commits and concurrent working-tree changes were not included
in this test. Do not treat this report as certification of a newer checkout.

## Installation method

- Windows host, Docker Desktop Linux engine 29.4.3, Compose 5.1.3.
- A `git archive` of tracked source was extracted into a separate directory.
  No developer `.env`, virtual environment, node_modules, database, or artifact
  directory was copied into the installation.
- Copied `deploy/.env.example`, replaced its three active placeholder secrets
  with independent random values, and selected unused loopback host ports.
- Built and started the documented base Compose stack, with no sandbox overlay,
  under the separate project `nodyra-fresh-20260911`. Its database, environment,
  artifact, and object-store volumes were created empty.
- Commands ran from the extracted source directory:

```sh
docker compose -p nodyra-fresh-20260911 -f deploy/docker-compose.yml config --quiet
docker compose -p nodyra-fresh-20260911 -f deploy/docker-compose.yml up --build -d
```

Test-only ports were web `25173`, API `28000`, PostgreSQL `25432`, Redis
`26379`, and MinIO `29000`/`29001`. CORS matched the selected web origin.

## Findings fixed

1. **Windows archive startup:** nginx's `15-nodyra-dns.envsh` was converted to
   CRLF in the exported source. nginx exited with a shell syntax error before
   serving the UI. `.gitattributes` now enforces LF for `*.envsh`, alongside
   the existing `*.sh` rule. A new export and rebuild started successfully.
2. **Release image naming:** the release workflow inherited mixed-case
   `Harshit-repo` in container repository names. The release job now normalizes
   all three GHCR image repositories to lowercase before the build/push steps.
   YAML parsing and step ordering were checked; successful registry publication
   remains unverified because Actions could not run.

Both corrections are committed in `f9398be6`.

## Verified user journeys

| Check | Result |
|---|---|
| Fresh database migration and API readiness | Passed: database and Redis healthy |
| Browser first-owner setup, workflow creation, Manual Trigger execution over HTTP | 3 passed |
| Documented `dataset_filter_export` starter template | All 6 nodes completed successfully |
| Starter artifact retrieval | 3 downloads verified against stored SHA-256 checksums |
| HTTPS browser journeys | All 11 passed: 9 in the main run, 2 in the corrected targeted rerun |
| Realistic workflows through HTTPS | 12/12 passed |
| Shutdown and restart with existing volumes | Owner login, workflow/run history, and artifact checksums retained |

The HTTPS browser checks cover credentials, desktop/mobile navigation in both
themes, running-status contrast, a 2 MiB upload/download, publication, schedules,
runner-pool configuration, and workflow execution. Two request-fixture tests
initially failed because Node could not resolve the test hostname; after adding
process-local mappings for both callback and promise DNS lookups, both passed.
These were test-harness failures, not an application fix or a disabled assertion.

The workflow cases were customer data processing, routing, extraction, health
checks, webhooks, failure recovery, cancellation, timeout, subworkflows, blocked
private egress, batching, and API export. Machine-readable case results are in
[workflow evidence](verification/selfhost-2026-09-11/workflows.json).

## Ingress test

Topology: test nginx TLS edge → shipped web nginx → API. A separate test
overlay set `CORS_ORIGINS=https://nodyra.test:28443`,
`PUBLIC_API_URL=https://nodyra.test:28443/api`, `TRUSTED_PROXY_COUNT=2`, and
`SESSION_COOKIE_SECURE=true`.

The hostname was resolved to loopback inside test processes. A temporary
certificate for `nodyra.test` was trusted explicitly by curl/Python/Node and by
its public-key fingerprint in Chromium. No public DNS, system hosts file,
system trust store, or public certificate issuance was changed. TLS validation
was not globally disabled.

Verified:

- HTTP 308 redirects preserve the requested path and query.
- Certificate-verified HTTPS, SPA deep links, and security headers work.
- Unauthenticated API requests are denied.
- Login sets Secure, HttpOnly, SameSite cookies; cookie-authenticated API calls work.
- Cross-origin cookie-authenticated mutation is rejected.
- New registration is closed after the first owner is created.
- `/api`, `/mcp`, and authenticated `/ws/runs/...` work across both proxies.
- Existing artifact downloads retain correct checksums.

See [ingress evidence](verification/selfhost-2026-09-11/ingress.json).
The test does not validate ACME issuance, public DNS, a Kubernetes ingress
controller, or a customer-specific reverse proxy configuration.

## Release distribution still requires action

At verification time:

- `Harshit-repo/Nodyra` was private; anonymous users could not clone/download it.
- `v1.0.0` existed as a tag, but there were no published GitHub releases.
- Release run `33423140004` did not start certification. GitHub's annotation
  reported failed account payments or a spending limit requiring attention.
- No published image digest, signature, attestation, or anonymous GHCR pull was
  verified in this run. Locally built images are not published-release evidence.
- The candidate was ahead of `main`; it was not merged or pushed during this check.

Before public self-hosted distribution, resolve the Actions account restriction,
choose repository/package visibility, merge the reviewed candidate, publish an
appropriate release tag, and verify a download/pull of those exact published
artifacts. Repeat the install gate for the actual final source/image revision.

The base stack remains for trusted authors: `EXECUTION_SANDBOX=auto` falls back
to subprocesses without the sandbox overlay. Bundled MinIO was exercised locally;
this does not overturn its documented production-storage qualification limits.
See [deployment guidance](deployment.md) for those boundaries.

## Local evidence and running test instance

The isolated source, build logs, browser traces, test-only certificate, and
reproduction helpers remain under `.tmp/selfhost-fresh-20260911` and adjacent
`.tmp/selfhost*` helpers. Secrets, private keys, and database contents are ignored
and are not committed. The test stack is left running for inspection. Stop it
without deleting its data by running from the extracted directory:

```sh
docker compose -p nodyra-fresh-20260911 -f deploy/docker-compose.yml \
  -f deploy/test-ingress.yml stop
```
