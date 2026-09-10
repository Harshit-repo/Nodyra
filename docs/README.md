# Nodyra documentation

## Start here

- [Getting started](getting-started.md) — run Nodyra and complete a first
  credential-free workflow.
- [Architecture chooser](architecture-chooser.md) — choose a topology based on
  trust, scale, and reliability requirements.
- [MCP quickstart](mcp-quickstart.md) — connect an MCP client and build visible,
  editable workflows.
- [Migration](migration.md) — generate compatibility reports before importing
  existing automation.
- [Licensing](licensing.md) — understand permitted self-hosting and commercial
  use.

## Build workflows

- [Writing nodes](nodes.md) — the `@node` decorator, uploaded modules,
  credentials, artifacts, typed values, and tests.
- [Working with datasets](datasetref.md) — artifact-backed DatasetRef handles,
  records conversion, and DuckDB SQL.
- [Workflow templates](workflow-templates.md) — template metadata, versioning,
  verification, and compatibility.
- [Recipes](recipes.md) — API, dataset, alerting, and CI examples.
- [Nodyra Academy](academy.md) — credential-free exercises using the loopback
  practice API.
- [Export modes](export-modes.md) — export a workflow to Python or a Docker
  bundle.
- [GitOps](gitops.md) — synchronize workflow definitions with GitHub.
- [MCP reference](mcp.md) — MCP transport, authentication, and tools.
- [Proof-point demos](demos/proof-points.md) — Python-native and MCP-native
  smoke demos.

## Operate Nodyra

- [Operations guide](operations/README.md) — install, configure, scale, secure,
  observe, back up, upgrade, and troubleshoot production deployments.
- [Deployment](deployment.md) — local development, Compose, Helm, configuration,
  and production checks.
- [Backup, restore, and upgrades](backup-restore.md).
- [Security policy](../SECURITY.md) — execution trust boundaries, operator
  responsibilities, and vulnerability reporting.
- [Workers and scaling](deployment/workers.md).
- [Disposable evaluation profile](hosted-evaluation.md).
- [Soak testing](soak-testing.md).
- [Rename migration](upgrading-to-nodyra.md) — update deployments and clients
  created before the project rename.

## Extend Nodyra

- [Integration development](integration-development.md) — provider operations,
  triggers, credentials, dynamic options, transport, and tests.
- [Provider coverage](provider-coverage-matrix.md) — official provider operation
  and trigger coverage.
- [Community nodes](community-nodes.md) — install and publish node packages.
- [Python accelerators](accelerators.md) — optional interpreter and compilation
  modes for workflow environments.

## Architecture and project policy

- [Architecture](architecture.md) — components, execution model, persistence,
  and security boundaries.
- [Architecture decision records](adr/).
- [Status matrix](status-matrix.md) — shipped, beta, experimental, scaffolded,
  and planned capabilities.
- [Continuous integration](ci.md).
- [Release and support policy](release-policy.md).
- [Contributing](../CONTRIBUTING.md), [CLA](../CONTRIBUTOR_LICENSE_AGREEMENT.md),
  [Code of Conduct](../CODE_OF_CONDUCT.md), and
  [Trademark policy](../TRADEMARK.md).
