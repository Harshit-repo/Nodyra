# Adoption and Pricing Experiment Contract

Nodyra records local-first, opt-in activation events and operational counters;
it does not send product telemetry unless the operator configures a sink.

## Funnel definitions

| Stage | Definition | Primary signal |
| --- | --- | --- |
| Workspace ready | Readiness has no blocking failure | production attestation |
| First value | First successful run with at least one finished node | activation event / run metrics |
| Activated | Successful run plus inspected output or artifact within 24 hours | activation events |
| Production intent | Published version or deployment created | activation event |
| Retained | Activated workspace returns and runs successfully in week 4 | telemetry warehouse |

Segment by hosted/self-hosted, template/import/blank start, team size, execution
posture, and release. Never segment on workflow payload, credential, node input,
or artifact content.

Registry, template, and migration supply/use are measurable through
`nodyra_registry_*`, `nodyra_template_instantiation_total`, and
`nodyra_migration_*`. Operators should export counters to a warehouse before
process restarts because Prometheus counters are process-local.

## Community posture experiment

The default Community treatment is five seats, ten active deployments,
unlimited local drafts, one sandboxed runner, three environments, and basic
observability. Enterprise value remains SSO/SCIM, shared multi-tenancy,
governance/policy, HA, advanced audit retention, external KMS, managed service,
and support.

Run any pricing test as a pre-registered experiment:

1. State the hypothesis, audience, treatment, minimum sample/window, primary
   activation/retention metric, guardrails, and stop conditions before launch.
2. Randomize at workspace level and keep product capability truth consistent.
3. Monitor support load, sandbox cost, install failure, and abuse alongside
   conversion; do not optimize conversion by degrading data portability.
4. Publish aggregated results and decision with confidence interval and known
   bias. Do not claim a winning treatment until the sample/window completes.
5. Roll back when reliability, security, or cost guardrails breach.

No pricing result is currently asserted by this repository. The Phase 7 gate
closes only after real experiment evidence is attached.
