# Upgrade and rollback runbook

## Before the upgrade

1. Confirm the source release is N-1 and supported by the compatibility matrix.
2. Generate the support bundle and archive the production attestation.
3. Pause new dispatches with the drain endpoint; wait until leased/running work reaches the agreed threshold.
4. Take an encrypted PostgreSQL backup and object-store inventory, then verify checksums and credential decryptability in an isolated restore.
5. Record current image digests, Helm values checksum, schema revision, runner protocol versions, and rollback owner.

## Roll forward

1. Deploy the migration job once. Database migrations are forward-only during the rollout window.
2. Roll API/control replicas, then workers, then the web client. Keep N-1 runners only while protocol negotiation confirms compatibility.
3. Run the release smoke: readiness, authentication, workflow create/publish/run, queue dispatch, artifact upload/download/checksum, metrics scrape, and evidence bundle.
4. Resume dispatch in a canary workspace. Advance only while error rate, queue age, worker churn, and artifact failures remain under the staged-rollout thresholds.

## Roll back

Application images may roll back to N-1 only when that release is declared forward-schema compatible. Do not run an Alembic downgrade against a production database unless the release notes explicitly authorize the exact revision and a restore has been rehearsed. If schema compatibility is not declared:

1. stop writes and preserve failed rollout evidence;
2. restore PostgreSQL and object storage together to the verified checkpoint;
3. deploy the recorded N-1 image digests and values;
4. verify credential decryptability, artifact integrity, queue invariants, and audit continuity;
5. reopen traffic gradually and retain the failed environment for incident review.

Rollback is complete only when the attestation has no failures, the smoke journey passes, queue age returns inside objective, and no duplicate/lost terminal runs are detected.
