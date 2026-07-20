# Production attestation and support evidence

Nodyra exposes two owner-only operational contracts:

- `GET /ops/production-attestation` evaluates configuration and live dependencies. Every check has a stable ID, category, severity, evidence, remediation, and verification time. A production rollout must have no `fail` checks. Warnings require attached external evidence or an accepted risk record.
- `GET /ops/evidence-bundle` returns a bounded JSON document containing posture, migration revision, dependency versions, queue and storage state, license posture, recent redacted failures, and the full attestation.

The API deliberately cannot prove external backup freshness, restore success, TLS termination, or provider-side encryption policy on its own. Those checks remain explicit warnings until the release evidence bundle attaches the operator-owned proof. The endpoint never returns connection strings, tokens, raw settings, or credential values.

Generate a private local bundle with:

```bash
export NODYRA_API_TOKEN='<owner token>'
python scripts/generate_support_bundle.py \
  --base-url https://nodyra.example.com \
  --output evidence/nodyra-support-bundle.json
```

The command caps the response at 10 MiB, validates the server's redaction declaration, never prints the token, and creates the file with owner-only permissions. Treat the output as sensitive operational metadata even though secrets are excluded.

Release review must add:

1. encrypted backup age and retention policy;
2. the latest restore-drill timestamp, observed RPO, and observed RTO;
3. TLS/proxy configuration evidence from the ingress or load balancer;
4. object-store encryption, versioning, and lifecycle evidence;
5. the production-attestation JSON and any warning dispositions.
