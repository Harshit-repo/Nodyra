# Launch image security assessment

Reviewed 2026-09-10. This is an applicability assessment for the tested
Linux/amd64 Compose installation, not a blanket vulnerability exception or
approval for a public multi-tenant service. No additional scanner suppression
has been added by this assessment. A release owner must review unresolved
findings against the exact deployed image and execution model.

## Scans and fixes

- The application Python dependency audit reports no known vulnerabilities.
- Vitest was updated to 4.1.11 for
  [GHSA-82fw-gwwq-j7x9](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9).
  The subsequent npm audit reports no known vulnerabilities.
- The web image installs `libuuid>=2.42.3-r1` from its signed Alpine 3.24
  repository. This removes the seven fixable high findings in the prior image.
- API and web scans pass the existing CI gate for fixable high/critical issues.
  This does **not** mean an unfiltered operating-system scan is empty.
- The unfiltered API scan recorded 60 package findings (55 high, 5 critical),
  representing 21 distinct advisories, with no fixed Bookworm version listed
  by that scan. Multiple binary packages share one vulnerable source package.

Local evidence: `.tmp/launch-api-all-high-0909.json`,
`.tmp/launch-base-applicability-0910.log`,
`.tmp/launch-trivy-api-current-0910.log`,
`.tmp/launch-trivy-web-patched-0909.log`,
`.tmp/launch-pip-audit-0909.json`, and
`.tmp/launch-npm-audit-fixed-0909.json`. These private local files must be
attached to the approved release evidence; repository prose is not a substitute.

## Applicability and remaining review

| Group | Evidence and assessment |
| --- | --- |
| MiniZip / CVE-2023-45853 | Debian explicitly states that the vulnerable MiniZip code is not built into the affected Bookworm zlib binary packages. The image also has no MiniZip package. This finding does not describe the installed zlib binary. [Debian assessment](https://security-tracker.debian.org/tracker/CVE-2023-45853). |
| Perl Archive::Tar, Storable, File::GlobMapper | Import probes in the image confirm these modules are absent. This excludes the reported module paths for CVE-2026-42496, CVE-2026-42497, CVE-2026-48962, CVE-2026-57433, and CVE-2026-9538 in this image. Installing extra Perl modules invalidates that assessment. [Archive::Tar advisory](https://security-tracker.debian.org/tracker/CVE-2026-42496). |
| Perl CVE-2026-8376 | The advisory requires a 32-bit Perl build. The tested image is x86_64 and Perl reports an eight-byte pointer size. Other architectures need their own assessment. [Debian advisory](https://security-tracker.debian.org/tracker/CVE-2026-8376). |
| Other Perl core findings | CVE-2026-13221 and CVE-2026-57432 remain in the package inventory. No application invocation of Perl was found in the API, core, node, or runtime source search. This limits exposure through the shipped application; it is not a claim that the installed interpreter is patched. Arbitrary user commands and added modules require separate review. |
| systemd-homed / CVE-2026-16742 | The image does not contain the `systemd-homed` executable or run a homed-managed login service. The reported vulnerable service is absent even though shared systemd libraries are installed. [Debian advisory](https://security-tracker.debian.org/tracker/CVE-2026-16742). |
| util-linux and ACL | CVE-2026-53613, CVE-2026-76642, CVE-2026-78408, CVE-2026-78409, CVE-2026-78410, and CVE-2026-54369 remain unpatched in the package inventory. They require privileged mount/namespace/ACL operations. API and worker PID 1 were verified as UID/GID 10001 with zero effective capabilities and `NoNewPrivs: 1`. These controls reduce exposure; they do not patch those binaries or cover a privileged host command. [Mount advisory](https://security-tracker.debian.org/tracker/CVE-2026-53613), [ACL advisory](https://security-tracker.debian.org/tracker/CVE-2026-54369). |
| GNU gzip and infocmp | CVE-2026-41992 concerns the GNU gzip command's mixed LZW/LZH processing; CVE-2025-69720 concerns the infocmp command. No invocation of these commands was found in the application source. User-authored shell commands remain outside that source-level finding. [gzip advisory](https://security-tracker.debian.org/tracker/CVE-2026-41992), [infocmp advisory](https://security-tracker.debian.org/tracker/CVE-2025-69720). |
| SQLite | CVE-2025-7458, CVE-2026-11822, and CVE-2026-11824 remain relevant to adversarial SQL or database files. Production control-plane storage in the tested stack is PostgreSQL. This does not qualify arbitrary SQLite workloads: require isolated execution for untrusted workflows and review SQLite use in their environments. Debian currently defers the FTS5 fixes in Bookworm. [FTS5 advisory](https://security-tracker.debian.org/tracker/CVE-2026-11822), [continuation-page advisory](https://security-tracker.debian.org/tracker/CVE-2026-11824). |

## Deployment conditions

### Supporting images checked on September 10

The expanded scan covered the actual PostgreSQL, Redis, socket-proxy, MinIO,
and data-quality Python sandbox images. The initial fixable high/critical
package-finding counts were 31, 0, 22, 100, and 32 respectively. These counts
are package findings, not distinct exploitable vulnerabilities.

`Dockerfile.postgres` and `Dockerfile.socket-proxy` now apply distribution
updates. The proxy has zero remaining fixable high/critical findings.
PostgreSQL's nine OS-package findings are cleared. Its remaining 22 findings
are Go-standard-library inventory entries in `/usr/local/bin/gosu`, verified
as gosu 1.19 built with Go 1.24.6. The reviewed startup utility changes the
process user and executes the database; it does not handle application HTTP,
TLS, XML, mail, or template data. The `os.Root` and Windows-network entries
also have no identified path in this Linux utility. This is a source-based
reachability assessment, not a rebuilt/patched Go binary or a scanner
suppression. Retain release-owner review of this disposition.
[gosu 1.19 source](https://github.com/tianon/gosu/blob/1.19/main.go).

The sandbox recipe now updates OS packages, pins uv 0.12.11, and uses image
schema v4 to prevent reuse of the older recipe. The rebuilt quality environment
has zero fixable high/critical findings. Evidence:
`.tmp/launch-scan-runtime-v4-0910.json`,
`.tmp/launch-scan-postgres-patched-0910.json`, and
`.tmp/launch-scan-docker-socket-proxy-patched-0910.json`.

The unfiltered sandbox scan still contains 54 OS-package findings (51 high,
3 critical), representing 18 advisories, on Debian 13.5. Its installed Perl
is 64-bit, and Archive::Tar, Storable, File::GlobMapper, and systemd-homed were
not present in the tested image. The remaining privilege-dependent utilities,
Perl core, and SQLite findings require their own deployment disposition; the
Bookworm control-plane assessment is not a blanket clearance for this runtime.
The sandbox runs as UID 65532 with the execution isolation controls described
below. Evidence: `.tmp/launch-runtime-v4-all-high-0910.json` and
`.tmp/launch-runtime-base-applicability-0910.log`.

The bundled MinIO image is **not qualified for production**. Its findings
include a MinIO session-policy bypass, and the community repository is
archived. The production hosting runbook now requires the external-S3 overlay,
which excludes both MinIO services. The local acceptance stack retains MinIO
only for synthetic data; this does not qualify the eventual external bucket.
[MinIO security](https://github.com/minio/minio/security),
[repository status](https://github.com/minio/minio).

Compose now sets `no-new-privileges:true` for API, worker, and web. The API and
worker drop to their service user before serving requests or executing jobs.
Required sandbox execution, read-only sandbox filesystems, dropped sandbox
capabilities, and separate run storage were exercised locally with Docker's
`runc` runtime. Local testing does not certify gVisor/Kata or the eventual
public host.

Do not reuse this assessment for privileged containers, host-network/root
workers, another architecture, additional system packages, or public untrusted
workflow authors without reviewing those changed conditions. Before GA,
identify the security-response owner, review the remaining package findings,
and retain the exact image digests, scans, deployment configuration, and
approved disposition. The existing `--ignore-unfixed` CI gate alone cannot
provide that approval.
