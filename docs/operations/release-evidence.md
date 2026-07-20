# Release Evidence Contract

The tag is a request to release, not proof that a release is safe. The release
workflow must finish certification before publishing images or a GitHub
release.

Required attachments are:

- Python/Web test summaries and static/template/version validation;
- fresh-schema migration result and N-1 upgrade evidence (or an explicit first
  release `not_applicable` record);
- engine benchmark and web bundle-budget reports with deltas called out;
- API, web, and runner SPDX SBOMs;
- image vulnerability scan output with no unaccepted critical/high finding;
- immutable image digests plus verified Cosign signatures and SBOM attestations;
- known issues, rollback notes, documentation version, source commit, and
  initial rollout stage.

`scripts/build_release_evidence.py` hashes every attachment into
`release-evidence.json`. The manifest is useful only when the referenced files
are attached beside it; a path/hash without the artifact is not evidence.

## Failure policy

Do not publish when a required artifact is missing, empty, mismatched to
`VERSION`, or generated from a different commit. Never mark a failed or skipped
gate as passed in prose. A justified exception is a structured
`not_applicable` record with owner/reason and must be removed when the condition
becomes applicable.

Release artifacts are retained at least as long as the support window. SBOMs,
signatures, provenance, and security advisories remain available for the life
of the distributed image.
