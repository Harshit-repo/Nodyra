# Nodyra documentation

Install Nodyra on your own infrastructure, build Python workflows, and inspect
every run. Start with the **1.0.5 self-hosted beta** for a single Docker host and
trusted workflow authors. There is no hosted Nodyra service.

## Install the current release

1. [Download 1.0.5](https://github.com/Harshit-repo/Nodyra/releases/tag/v1.0.5)
   and verify the source archive with the attached `SHA256SUMS`.
2. Follow the [single-host installation guide](deployment/self-hosted.md) to
   configure secrets, build the containers, and create your owner account.
3. Run the [first workflow](getting-started.md#4-run-a-verified-starter-template)
   and download its verified artifacts.

The release builds images locally. See [release verification](releases/1.0.5.md)
for the tested scope, evidence, and limits. Configure HTTPS and backups before
allowing access from other machines.

## See Nodyra in action

![Nodyra product tour showing a workflow run, node inspector, and Python editor](nodyra-product-tour.gif)

The tour shows a real local workflow execution. The latest release also
improves the input/output header layout shown in the recording.

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
