# Supported production compatibility matrix

This matrix is the release contract for Nodyra `0.1.x`. A combination outside these ranges may work, but is not a production-supported target until CI and upgrade evidence cover it.

| Component | Supported | Required production posture |
|---|---|---|
| Python | 3.12–3.14 | API, workers, runner agent, and migration job use the same minor line per rollout |
| PostgreSQL | 15–17 | Managed or replicated; TLS in transit; non-superuser application role; tested backups |
| Redis | 7.2–8.x | Persistent/shared endpoint; TLS or private network; eviction disabled for coordination keys |
| S3 API | AWS S3 and S3-compatible APIs implementing multipart upload, metadata, range reads, and batch delete | Versioning, encryption, lifecycle, and recovery procedure enabled |
| Kubernetes | 1.29–1.33 | Pod security, NetworkPolicy, disruption budgets, and a supported ingress controller |
| Helm | 3.14–3.18 | `helm lint`, template validation, install, upgrade, rollback, and teardown evidence |
| Docker Engine / compatible API | 25–28 | Rootless daemon or bounded socket proxy for sandbox workers; never expose an unrestricted remote daemon |
| Chromium browsers | Current and previous two stable releases | JavaScript, secure cookies, WebSocket, `Intl`, and CSS forced-colors support |
| Firefox | Current and previous two stable releases | Same browser requirements |
| Safari | Current and previous major release | Same browser requirements |
| Runner protocol | `1.0`; rolling compatibility with `0.0` during one release window | Server and agents negotiate before dispatch; unsupported versions fail before execution |
| Python client | `nodyra-client 0.2.x` with server `0.1.x` | Client sends its own version; incompatible public API changes require a documented client release |

Support policy:

- Database schema upgrades support N-1 to N. Skipping releases requires sequential migrations.
- The web client and API must be from the same release during steady state; rolling upgrades may overlap by one release.
- Runner agents may lag the server by one release only while their negotiated protocol remains supported.
- Exported workflow JSON and artifact bytes are the data escape hatch. Operators must verify an export before destructive downgrade or decommission.
