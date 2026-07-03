# Nodyra docs

- [Architecture](architecture.md) — components, execution model, schema, security.
- [Deployment](deployment.md) — local/compose/Helm, config flags, security checklist.
- [Writing a node](nodes.md) — built-in `@node` decorator, code modules
  (upload-to-nodes), artifacts API, typed values, testing.
- [Building V2 integrations](integration-development.md) — spec-driven
  provider operations/triggers, credentials, dynamic options, transport, and
  tests.
- [Provider coverage matrix](provider-coverage-matrix.md) — current official
  provider operation/trigger coverage and next provider targets.
- [Production integration nodes plan](production-integration-nodes-plan.md) —
  phased roadmap for production-ready official provider node packs.
- [Production AI/ML nodes plan](production-ai-ml-nodes-plan.md) — production
  roadmap for fine-tuning, evals, synthetic data, RAG lifecycle, local training,
  serving, monitoring, and model governance nodes.
- [DatasetRef guide](datasetref.md) — table handles, records↔dataset conversion,
  DuckDB SQL, quick fixes, and common workflow patterns.
- [n8n vs Nodyra comparison](n8n-vs-nodyra-comparison.md) — architecture,
  performance, UI/UX polish plan, and node roadmap.
- [Architecture improvement plan](architecture-improvement-plan.md) — production
  runtime modes, durable queueing, runner leases, artifacts, observability,
  credentials, and UX hardening plan.
- [Status matrix](status-matrix.md) — per-task shipped/beta/scaffolded/planned
  state of the architecture improvement plan.

Live milestone status is tracked in
[production-automation-platform-plan.md](production-automation-platform-plan.md).
