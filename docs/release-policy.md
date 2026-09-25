# Version, Release, and Support Policy

Nodyra follows Semantic Versioning for the product release: API, control plane,
web app, core engine, node library, runtime, importer/exporter, runner agent,
container images, and Helm chart share the version in the root `VERSION` file.
The separately distributed `nodyra-client` may version independently; its
supported server range is published in the compatibility matrix and release
notes.

## Release stages

| Stage | Entry criteria | Exit criteria |
| --- | --- | --- |
| Alpha | Architecture is coherent; destructive changes remain possible | Critical user journey works; threat model and migration design reviewed |
| Beta | Feature complete for stated scope; backup/restore and observability exist | Release gates, upgrade from supported N-1, docs, and production-like certification are green |
| RC | No planned feature changes; known issues and rollback notes published | Five consecutive production-like runs meet budgets; no open release blocker |
| GA | Support, security response, compatibility, and staged rollout are staffed | Scorecard remains within SLO; regression reopens the affected category |

The current release is **1.0.5, a self-hosted beta**. The numeric version alone
does not announce GA, an LTS commitment, or support for every deployment
topology. The [release verification page](releases/1.0.5.md) defines its tested
single-host, trusted-author scope. Breaking changes require a changelog entry,
migration path, and deprecation when technically feasible.

## SemVer contract

- Patch: backward-compatible bug/security fix; no schema or protocol break.
- Minor: backward-compatible capability, additive API/schema change, or a
  pre-1.0 change with documented migration and deprecation.
- Major: incompatible public API, workflow format, persistent schema,
  extension contract, or runner protocol removal.

Published workflow/template/package versions are immutable. Database migrations
are forward-only in normal operation. The API and worker support a rolling
overlap of one product release; runner protocol compatibility is negotiated and
maintained for at least that same window.

## Support window

Before GA, only the latest Beta/RC receives fixes; critical security fixes may
be backported to the immediately previous release at maintainer discretion.
After GA, the current minor receives full support and the previous minor
receives critical/security fixes for 90 days after supersession. Patch users
must run the newest patch in their supported minor.

There is no announced LTS line. An LTS decision must name the supported
minor, duration, infrastructure matrix, backport policy, and funding/owner; the
absence of that announcement means standard support only.

## Deprecation and security

Public API, configuration, node, and protocol deprecations are announced in the
changelog and runtime/UI warnings. Removal occurs no sooner than the next minor
and 90 days after announcement, except for actively exploited or unsafe
behavior. Security fixes may disable behavior immediately; release notes must
explain impact and recovery without exposing exploit details prematurely.

Security advisories identify affected/fixed versions and mitigations. A release
is not supported when it has a known unmitigated critical vulnerability or its
infrastructure version is outside the compatibility matrix.

## Required release package

The automated image-release process is intended to attach test and migration evidence, upgrade/rollback result,
SBOMs, image digests, signatures/provenance verification, vulnerability scan,
bundle/benchmark deltas, known issues, documentation version, and rollout
stage. See [release evidence](operations/release-evidence.md) and
[staged rollout](operations/staged-rollout.md).

The 1.0.5 beta uses a **source-only distribution**: source archives, SHA-256
checksums, local fresh-install acceptance evidence, and local vulnerability
reports. Account billing blocked hosted jobs before they started, so it does
not claim hosted certification, signed prebuilt images, SBOM attestations, or
the full automated image-release evidence set. This is a documented limitation
of this release, not a removal of the image-release security gates.
