# Changelog

## Unreleased

### Added

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
