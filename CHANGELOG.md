# Changelog

## 1.0.4 - 2026-09-25

- Keep full image-signature and SBOM-attestation verification evidence in the
  release asset without streaming large payloads into GitHub Actions log masking.
- Bound the verification step to 15 minutes while retaining all security gates.
- Include the self-hosted runtime and editor improvements from 1.0.3. Its tag
  passed certification and vulnerability scans, but publishing was cancelled
  after attestation output stalled in the Actions log processor.

## 1.0.3 - 2026-09-25

- Move API and worker images to a digest-pinned, patched Python 3.14.7 base
  with glibc compatibility and an updated gosu privilege-switching runtime.
- Apply the release's full high/critical vulnerability gate in CI before tagging.
- Use a patched, digest-pinned nginx image with non-root startup and support for
  the chart's read-only filesystem and writable configuration/cache volumes.
- Bundle the Python editor and worker locally so the production security policy
  and deployments without access to public script CDNs can load them.
- Fix full-editor button clicks, page-error recovery, and invalid public-chat URLs.
- Add production browser coverage for the Python editor and lazy loading.
- The v1.0.2 tag passed functional certification but did not publish a release
  because its base image failed the stricter release vulnerability scan.

## 1.0.2 - 2026-09-25

- Fix release publishing by pinning the Cosign installer to its verified
  v4.1.2 commit. The v1.0.1 tag did not produce a published distribution.
- Include all self-hosted improvements listed under 1.0.1 below.

## 1.0.1 - 2026-09-25

- Self-hosted installation with persistent local artifacts and documented HTTPS setup.
- Restore bundled S3 installs by building pinned official MinIO source releases
  instead of relying on retired registry images. The local-storage profile needs
  no MinIO service.
- Include Debian PCRE2 security updates in the Python container images.
- Update AnyIO and Soup Sieve to resolve newly reported security findings.
- Bind configured webhook timestamps to their HMAC signatures to prevent replay
  by replacing the timestamp on a captured payload.
- Improve runtime callbacks, environment rebuild handling, loop execution,
  pinned outputs, and MCP graph validation.
- Include source archives, checksums, certification evidence, vulnerability
  reports, SBOMs, and signed container images with the release.

## 1.0.0 - 2026-08-30

### Security

- Password hashing moved from PBKDF2-HMAC-SHA256 to **Argon2id**. Existing
  hashes keep verifying and are re-hashed transparently on the owner's next
  successful sign-in, so no one is signed out.
- Fixed an account-enumeration timing oracle on `POST /auth/login`: the
  stand-in hash used when an email matched no user was a bcrypt literal that no
  Nodyra hasher recognised, so it was rejected on a format check in
  microseconds while a real account paid the full KDF cost.
- Pre-JWT (legacy HMAC) session tokens are no longer accepted by default. Set
  `AUTH_ACCEPT_LEGACY_TOKENS=true` for the one release in which tokens minted
  before the JWT migration are still inside their TTL.
- An agent request carrying two tool calls with the same id let a single
  operator approval authorise both, and the second may be a different,
  side-effecting tool. Model-generated ids come from a context injected content
  can steer, so uniqueness was not something to assume. Duplicate ids are now
  refused outright — they also collapsed two calls into one replayed result
  when a paused run resumed.
- The SAML ACS endpoint parsed the posted assertion with lxml's default
  parser, which expands entities declared in the internal subset. The endpoint
  is unauthenticated and the signature is verified only after parsing, so a
  279-byte nested-entity document amplified ~2,300x before any check ran. The
  input caps bound the request body; nothing bounded what parsing turned it
  into. Now parsed with entities, DTDs and network access disabled.
- Credential "Test connection" fetched the URL from the credential the user
  had just typed through a bare HTTP client, bypassing the SSRF guard every
  other user-controlled fetch uses. An editor could point a credential at
  `http://169.254.169.254/…`, press Test, and get cloud-metadata responses
  reflected back. Now routed through `assert_public_http_url`, honouring the
  same `NODYRA_ALLOW_PRIVATE_EGRESS` switch as node egress.
- Startup now refuses a plaintext `VAULT_URL` outside loopback. Vault Transit
  receives the plaintext org KEK and returns it, so `http://` put the key this
  provider exists to protect — and the Vault token — on the wire in the clear.
  The provider's own docstring used `http://vault:8200` as its example. Set
  `VAULT_ALLOW_INSECURE_TRANSPORT=true` to accept that on a trusted network.
- `GET /ws/runs/{run_id}` authenticated the caller but never checked the run
  belonged to their organization, so in a multi-tenant deployment any signed-in
  principal who learned a run id could stream that run's live events, including
  node outputs. The socket now resolves the org and looks the run up through the
  org-scoped filter, matching the workflow socket.
- WebSocket connections ignored `auth_bind_token_to_ip`: the HTTP middleware
  that enforces it does not run for upgrades, so a token pinned to another
  address still opened a live stream. Both sockets now verify it.
- Archive extraction checked containment with a string prefix, so a member
  named `../out-evil/payload` escaped into a sibling directory whose name merely
  started with the target's (`/tmp/out-evil` is not inside `/tmp/out`, but the
  target is a literal prefix of it). Now compared with `Path.relative_to`.
  `archive_extract` was one of the nodes that had no test of any kind.
- Three state-changing routes had no authorization dependency, making them
  reachable by any authenticated principal regardless of role — including
  `POST /artifacts/{id}/query`, which executes a user-supplied DuckDB query.
  Tenancy was already enforced on all three; only role was missing. Now guarded,
  with a static gate asserting every mutating route is authorized or explicitly
  exempt with the mechanism that authenticates it instead.
- The licence-refresh endpoint is unauthenticated by necessity and was
  unthrottled. A 256-bit token is not guessable, but every call cost a database
  lookup and an Ed25519 signature. Now rate-limited per IP.
- Row-level-security policies added for `environment_build_jobs`,
  `memberships`, `workflow_checks` and `workflow_revisions`, which carried an
  `org_id` but had no policy. A test now fails if any org-scoped model ships
  without one.
- Artifact redaction resolves the owning organization before loading secrets,
  so a single run no longer decrypts every tenant's credentials.
- Artifact `storage_key` is validated against the keys the producing run could
  legitimately have written, instead of being trusted from the run's own code.
- Audit events are now recorded for queue drain, dead-letter replay, runtime
  pool resize, registry package installs, runner-pool and runner lifecycle,
  SSH onboarding, MCP connection changes, custom roles, environment package
  changes, credential OAuth starts and tests, deployment runs, GitHub pulls,
  run cancellation, approval decisions, and sign-in/sign-out.
- Helm pods run as non-root with a read-only root filesystem, dropped
  capabilities, `RuntimeDefault` seccomp and no mounted service-account token,
  satisfying the `restricted` Pod Security Standard.

### Fixed

- **The launch pages advertised entitlements the licence does not grant.** Both
  landing pages had drifted from `TIER_DEFAULTS` independently, and nothing was
  watching. The Pro card sold "Audit log export" and "RBAC", which
  `licensing.py` grants only to Enterprise — a customer paying $49 would not
  have got either. Both pages sold sandboxed execution as a paid upgrade when
  it is a Community entitlement, which understated the free tier and would have
  made the first Pro invoice look like it bought nothing. Enterprise advertised
  SCIM provisioning, which is not implemented anywhere. `index.html` also
  understated Community's caps as 2 seats and 3 deployments, against a real 5
  and 10. Every tier card now states the caps from `TIER_DEFAULTS`, and
  `test_pricing_page_truth.py` fails the build — including the release job — if
  a page ever claims a feature its tier does not grant, disclaims one it does,
  states a cap the licence contradicts, or names a capability that does not
  exist.
- The Enterprise section called the audit log **immutable**. Nothing in the
  product updates an audit row, but a retention job deletes old ones and
  nothing enforces immutability at the storage layer, so the claim would not
  survive a security review. It now says append-only and explains the retention
  window. The same section described the credential vault as "Fernet
  AES-128-CBC, scoped per environment", which named the primitive and missed
  the design: envelope encryption with a per-credential data key wrapped by a
  per-organisation key wrapped by a master key, with an optional external KMS
  that keeps the master key out of Nodyra's storage entirely.
- `REPLACE-ORG/REPLACE-REPO` survived into eight links on the launch page,
  including the one the fair-code notice points at, so the License link would
  have 404ed on day one. Placeholders are now a test failure.
- Removed `brand/homepage/index - Copy.html`, a tracked byte-for-byte duplicate
  of the landing page that carried the stale claims and would have been served
  alongside the real one.

- **README and the deployment guide described a product from before
  sandboxing existed.** "Multi-tenant SaaS isolation would require additional
  sandboxing such as containers, gVisor, Firecracker" and "treat workflow
  authors as trusted automation engineers unless stronger runtime sandboxing is
  added" both read as though the feature were unbuilt — while the same README
  already said, forty lines earlier, that multi-tenancy refuses to boot without
  it. Both passages now state what is actually true and, more usefully, that
  `EXECUTION_SANDBOX` ships as `off`: multi-tenancy promotes it to `required`
  automatically, but a single-tenant deployment runs workflow code in the worker
  process on the host until an operator sets it. Understating your own isolation
  is not the safe direction of error — an operator who believes the feature does
  not exist never goes looking for the switch.
- SCIM was listed as a licensed Enterprise feature in README.md and ticked in
  the tier matrix in `docs/deployment.md`. It is not implemented anywhere.
  `test_pricing_page_truth.py` now checks the customer-facing docs for it too,
  and a companion test removes the ban automatically if the capability is ever
  actually built.

- Two shipped templates set `timezone` on the schedule trigger, whose parameter
  is `tz` — the setting was silently ignored and those workflows ran in the
  server timezone. A third was tagged as a starter while requiring a Slack
  credential.
- `GET /health/ready` now verifies the database schema is at the migration head
  rather than only that the connection works. An unmigrated database previously
  reported healthy while every write returned 500 — in Kubernetes, where
  migrations run as a separate job, that let a broken pod take production
  traffic.
- Environment package specifiers carrying a PEP 508 environment marker (for
  example `zxing-cpp>=2.2; sys_platform=='win32'`, which package preflight
  tells users to add) were rejected by the sandbox image builder. Specifiers
  are now parsed with `packaging` and shell-quoted at interpolation.
- Loading skeletons announced nothing to screen readers: `aria-label` on a bare
  `div` is prohibited, so the label was dropped. They are now `role="status"`
  live regions.
- The MQTT trigger's `qos` default (`1`) did not match its string choices, so
  the dropdown rendered with nothing selected.

### Added

- **Self-serve subscriptions.** Licence keys were minted by hand, which works
  for one customer at a time and not for a subscription: nothing renewed a key,
  nothing revoked it when a card failed, and nothing recorded who had what.
  Added a subscription registry, Stripe checkout and webhook handling with
  idempotent event processing, signed issuance driven by a subscription rather
  than a person at a terminal, and an opt-in customer-side loop that renews a
  licence before it lapses. Offline verification is unchanged, and an
  air-gapped deployment still never reaches out. See `docs/subscriptions.md`.
- **In-product plan comparison.** A Community user who hits a cap can now see
  which edition lifts it, served from the same tier definitions the enforcement
  code reads so the two cannot drift.
- **Workflow templates: 4 to 16.** Twelve new templates spanning monitoring,
  ETL, data quality, document extraction, batching, branching and error
  handling. All but one run with no credentials, so a new user can press run in
  their first minute.
- Blank tool-param descriptions are filled from the param's own declared
  choices or a vocabulary of names that mean the same thing everywhere, taking
  the undocumented count from 702 to 332 without inventing any text. Ambiguous
  names (`state`, `right`, `type`) are deliberately excluded and tested for.
- Contract harness covering all 512 registered nodes (6,147 assertions):
  manifest/signature agreement, JSON serialisability, port and choice
  validity, PEP 508 requirements, and tool legibility. 100 nodes previously
  had no test of any kind.
- Automated accessibility checks (axe-core) over sign-in, first-run setup,
  dialogs and shared primitives.
- Process resource gauges (`nodyra_process_resident_bytes`, `_open_fds`,
  `_threads`) sampled at scrape time, plus an endurance harness that holds
  sustained load and reports resource drift. Its leak detector distinguishes a
  warm pool filling to its ceiling from a genuine leak by whether the series is
  still rising at the end — without that, Nodyra's own worker pool reads as a
  descriptor leak. Verified against a live instance: over eight minutes of load
  on a warm process every gauge declined.
- The AI builder eval is now a blocking CI gate at a 1.0 threshold rather than
  advisory. The planner is deterministic, so any drop is a real regression.
- Engine **throughput** benchmark as a CI budget gate, alongside the existing
  planning benchmark: 2,100-3,500 nodes/sec, with a case that fails if
  node-level concurrency is lost.
- Helm chart gains a ServiceAccount, PodDisruptionBudgets (above one replica),
  optional HorizontalPodAutoscalers and NetworkPolicies, and startup probes.
- Renovate configuration so dependency advisories arrive as pull requests.
- Production posture attestation and redacted operator evidence bundles.
- Artifact reconciliation, health, lineage, streaming upload, and multipart S3 support.
- Signed community registry installs with versioned upgrade/rollback lifecycle.
- Searchable verified templates and static migration compatibility reports.
- Disposable hosted-evaluation deployment profile and adoption measurement contract.

### Changed

- Product packages, API, web app, runner, images, and Helm chart align on
  `0.1.0`; the Python client keeps its independent `0.2.x` line.
- Community defaults allow five seats, ten active deployments, three
  environments, and one sandboxed runner.

### Security

- Registry distributions require immutable HTTPS URLs, SHA-256 pins, signed
  manifests, trusted publisher roots, and explicit permission/compatibility
  metadata in production.

## 0.1.0 - Beta baseline (release candidate)

Noodle is now **Nodyra**. See [docs/upgrading-to-nodyra.md](docs/upgrading-to-nodyra.md)
for the operational one-pager: queue drain, session cookie rename, dev DB reset,
and `NOODLE_*` -> `NODYRA_*` env vars with one-release fallbacks.
