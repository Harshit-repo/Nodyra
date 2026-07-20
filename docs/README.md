# Nodyra Documentation

## Start here

- [Getting started](getting-started.md) — zero to first inspected workflow in
  about five minutes once images are available.
- [Architecture chooser](architecture-chooser.md) — trusted local, internal
  production, multi-tenant, regulated, and air-gapped paths.
- [Migration guide](migration.md) — static compatibility reports for Nodyra,
  Python, n8n, Airflow, and Prefect sources.
- [MCP quickstart](mcp-quickstart.md) — let Claude/Cursor build workflows on
  your instance (61 tools).
- [Licensing guide](licensing.md) — what's free, what's paid, in plain English.

## Using Nodyra

- [Writing a node](nodes.md) — the `@node` decorator, uploaded code modules,
  artifacts API, typed values, testing.
- [Working with datasets](datasetref.md) — DatasetRef table handles,
  records↔dataset conversion, DuckDB SQL, common patterns.
- [Community nodes](community-nodes.md) — installing and publishing node packs.
- [Workflow template catalog](workflow-templates.md) — metadata, versioning,
  verification, screenshots, and compatibility rules.
- [Copy-paste recipes](recipes.md) — API, dataset, alerting, and CI patterns.
- [Nodyra Academy](academy.md) — credential-free exercises with a deterministic
  loopback practice API.
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
- [Disposable evaluation profile](hosted-evaluation.md).
- [Soak testing](soak-testing.md).
- [Upgrading to Nodyra](upgrading-to-nodyra.md) — migrating from the former product name
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
- [Release and support policy](release-policy.md) — SemVer, alpha/beta/RC/GA,
  deprecation, supported versions, and security maintenance.
- [ADRs](adr/) — architecture decision records.
- [Status matrix](status-matrix.md) — shipped / beta / scaffolded / planned.
- [Adoption metrics](adoption-metrics.md) — funnel definitions and pricing
  experiment contract.

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
