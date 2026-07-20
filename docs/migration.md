# Migrate Workflows to Nodyra

Open **Workflows → Import**, choose a source format, and run **Analyze
compatibility**. Analysis parses source statically and never executes it.

The report classifies every discovered unit:

- `exact`: reconstructed without semantic change;
- `transformed`: converted to a Nodyra node with a documented adaptation;
- `manual`: a safe draft can be created only after explicit partial-import
  approval and operator review;
- `unsupported`: omitted because Nodyra cannot defend equivalent behavior.

Imports with manual or unsupported findings are blocked by default. Partial
imports create an unpublished draft; reconnect credentials, install required
packages, inspect every edge, and run representative fixtures before publish.

## Supported paths

| Source | Current support |
| --- | --- |
| Nodyra module export | Exact AST-based round trip |
| Standalone Python | Manual trigger plus an inspectable Code node; imports are environment prerequisites |
| n8n | Manual/webhook/schedule triggers, HTTP Request, Set, and Slack common patterns; JavaScript and unknown nodes remain manual/unsupported |
| Airflow | Partial drafts for top-level `PythonOperator`, literal `BashOperator`, empty operators, and simple `>>`/`<<` dependencies |
| Prefect | Partial drafts for synchronous top-level `@task` calls and direct data dependencies inside one `@flow` |

Airflow and Prefect always require explicit partial-import approval because
schedule/deployment, retry, state, cache, secret, concurrency, dynamic mapping,
and hook semantics need human review. Unsupported/dynamic patterns remain in
the report and are never silently executed or translated.

## Production cutover

1. Freeze changes in the source workflow and archive its definition/version.
2. Import to an isolated Nodyra environment and resolve every report finding.
3. Replace embedded secrets with scoped Nodyra credentials.
4. Compare fixtures, failure behavior, retries, timeouts, schedules, webhooks,
   and artifact checksums.
5. Publish an immutable Nodyra version, deploy it disabled, then canary traffic.
6. Keep the source workflow available for rollback until the observation window
   closes; never run both sides on non-idempotent events without deduplication.

Migration funnel metrics are exposed as `nodyra_migration_preview_total` and
`nodyra_migration_import_total`, using bounded format/outcome labels.
