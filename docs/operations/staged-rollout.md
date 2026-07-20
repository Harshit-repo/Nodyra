# Staged Rollout and Automatic Halt Policy

Promote the same immutable image digests through four stages. Rebuilding
between stages invalidates earlier evidence.

| Stage | Audience | Minimum observation | Promotion requirement |
| --- | --- | --- | --- |
| Internal/demo | Maintainers and synthetic traffic | 30 minutes | Smoke, migrations, artifact round trip, readiness, metrics |
| Pilot | Explicit design partners | 24 hours | No rollback threshold; support owner available |
| Canary | 5–10% of eligible production traffic/workers | 60 minutes and peak sample | Error/latency/churn/storage/UI gates green |
| Broad | Remaining eligible population | 24-hour heightened watch | Canary evidence attached and rollback ready |

## Automatic halt/rollback thresholds

Compare canary to both an absolute ceiling and the stable baseline. Halt
promotion when any is true for two consecutive five-minute windows:

- API 5xx ratio above 1% or more than 2× stable;
- workflow terminal error ratio above 5% or more than 2× stable, excluding
  declared synthetic failures;
- queue p95 wait above 60 seconds or more than 2× stable;
- worker restart/churn above 3 per replica per 15 minutes;
- artifact store/read failures above 0.5%;
- browser smoke failure or a release bundle budget breach;
- readiness failure on any canary replica for more than five minutes.

On breach, stop new promotion, preserve evidence, drain when possible, then
roll back application images with `helm --atomic` or the deployment controller.
Schema rollback follows the separate upgrade contract—never automatically run
database downgrade migrations.

Use `scripts/evaluate_rollout.py` with a captured signal JSON document. It exits
non-zero on `halt`, produces a machine-readable decision, and records every
breached threshold. Human approval is still required to move from pilot to
canary and canary to broad.

The **Promote immutable release** workflow deploys digest-pinned API/worker and
web images with `helm --atomic`, waits for every rollout, evaluates provided
consecutive signal windows for canary/broad, and invokes Helm rollback on a
failed gate. Protect the `nodyra-pilot`, `nodyra-canary`, and `nodyra-broad`
GitHub environments with the appropriate human approvers and scoped
`KUBECONFIG_BASE64` secret.
