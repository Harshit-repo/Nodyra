# Choose a Nodyra Architecture

Choose from the trust boundary and reliability target, not workload size alone.

| Situation | Topology | Execution posture | Data services | Start here |
| --- | --- | --- | --- | --- |
| One trusted engineer evaluating locally | Docker Compose on loopback | Trusted or sandbox overlay | Bundled PostgreSQL, Redis, MinIO | `make demo` |
| One trusted internal team | Split API and worker | `auto` for mixed code, `required` for generated code | Managed PostgreSQL/Redis/object storage | Helm baseline |
| Untrusted authors or multiple tenants | Dedicated API, workers, and sandbox runtime | `required` | Managed HA services, tenant RLS and scoped keys | Enterprise multi-tenant review |
| Regulated production | HA control plane, isolated worker pools | `required`; separate sensitive pools | Encrypted managed services, external KMS | Production attestation |
| Air-gapped environment | Private images/indexes, registry disabled | Policy-selected | Internal PostgreSQL/Redis/object storage | Offline operations runbook |

## Decision sequence

1. If any workflow author or generated source is untrusted, require the
   container sandbox. Do not use in-process execution as a cost shortcut.
2. If the API and worker are separate processes, use Redis queueing and a
   strong `INTERNAL_API_TOKEN`.
3. If losing one host is unacceptable, use managed HA data services and at
   least two API/worker replicas; verify scheduler leadership and queue leases.
4. If artifacts are operational records, use S3-compatible storage with
   versioning/retention and back it up consistently with PostgreSQL.
5. If multiple organizations share the control plane, enable multi-tenancy,
   RLS, sandbox-required execution, per-org keys, quotas, and fairness together.

Before exposure, request `/ops/production-attestation`, resolve every failure,
and attach operator-owned backup, restore, TLS, and KMS evidence. Compatibility
and upgrade contracts live in [the operations matrix](operations/compatibility-matrix.md).
