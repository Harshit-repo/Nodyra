# Nodyra Documentation

## Start here

- [Getting started](getting-started.md) — zero to first workflow in ~10 minutes.
- [MCP quickstart](mcp-quickstart.md) — let Claude/Cursor build workflows on
  your instance (61 tools).
- [Licensing guide](licensing.md) — what's free, what's paid, in plain English.

## Using Nodyra

- [Writing a node](nodes.md) — the `@node` decorator, uploaded code modules,
  artifacts API, typed values, testing.
- [Working with datasets](datasetref.md) — DatasetRef table handles,
  records↔dataset conversion, DuckDB SQL, common patterns.
- [Community nodes](community-nodes.md) — installing and publishing node packs.
- [Export modes](export-modes.md) — workflow → Python script / Docker bundle.
- [GitOps](gitops.md) — two-way GitHub sync for workflow definitions.
- [MCP reference](mcp.md) — full MCP server/tool documentation.
- [Product proof-point demos](demos/proof-points.md) — Python-native,
  MCP-native, and AI-inspectable smoke demos.

## Operating Nodyra

- [Operations guide](operations/README.md) — install, configure, scale, secure,
  observe, back up, upgrade, and troubleshoot production deployments.
- [Deployment](deployment.md) — local, Docker Compose, Helm; config flags;
  production checklist.
- [Backup, restore & upgrades](backup-restore.md).
- [Security policy](../SECURITY.md) — trust model, execution postures,
  operator responsibilities, reporting vulnerabilities.
- [Workers & scaling](deployment/workers.md).
- [Soak testing](soak-testing.md).
- [Upgrading to Nodyra](upgrading-to-nodyra.md) — migrating from the Noodle
  working name.

## Extending Nodyra

- [Building V2 integrations](integration-development.md) — spec-driven provider
  operations/triggers, credentials, dynamic options, transport, tests.
- [Provider coverage matrix](provider-coverage-matrix.md) — official provider
  operation/trigger coverage.

## Architecture & internals

- [Architecture](architecture.md) — components, execution model, schema,
  security boundaries.
- [Continuous integration](ci.md) — CI lanes, flake retry reporting, and
  expected maintainer response.
- [ADRs](adr/) — architecture decision records.
- [Status matrix](status-matrix.md) — shipped / beta / scaffolded / planned.

## Project & strategy (maintainers)

- [Licensing & monetization report](licensing-and-monetization-report.md) —
  the license decision and its rationale.
- [Licensing plan](licensing-plan.md) — tier limits and offline license-key
  design. [Internal notes](licensing-internal.md).
- [n8n vs Nodyra](n8n-vs-nodyra-comparison.md) ·
  [Windmill vs Nodyra](nodyra-vs-windmill-comparison.md).
- Roadmaps: [integrations](production-integration-nodes-plan.md) ·
  [AI/ML nodes](production-ai-ml-nodes-plan.md) ·
  [platform](production-automation-platform-plan.md) ·
  [architecture improvements](architecture-improvement-plan.md).
- Contribution intake: [CONTRIBUTING](../CONTRIBUTING.md) ·
  [CLA](../CONTRIBUTOR_LICENSE_AGREEMENT.md) ·
  [Code of Conduct](../CODE_OF_CONDUCT.md) ·
  [Trademark policy](../TRADEMARK.md).
