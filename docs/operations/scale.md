# Scale

## Topologies

- Single process: `DISPATCH_ROLE=inline`, `QUEUE_BACKEND=none`. Suitable for
  local development and small trusted installs.
- Split control plane: API replicas accept HTTP traffic; standalone workers run
  `python -m app.worker_main`; Redis backs queue and fanout.
- Remote runner pools: machines run `nodyra-runner` and connect outbound over
  WebSocket. Use this when compute lives outside the API cluster.

## Production Rules

- Use PostgreSQL and Redis before scaling past one replica.
- Run exactly one scheduler leader.
- Set `INTERNAL_API_TOKEN` for any split topology.
- Keep API replicas stateless: artifacts, environments, and database state must
  live on shared storage or object storage.
- Size `MAX_CONCURRENT_RUNS`, worker replica count, and runner pool max together
  so queue depth drains without exhausting CPU or memory.
- Prefer sandbox-required workers for untrusted code; warm pools reduce cold
  starts but do not replace container limits.

## Operational Checks

```bash
curl -fsS https://<host>/ops/runtime-mode
curl -fsS https://<host>/ops/queue
curl -fsS https://<host>/metrics
```

The readiness panel should report `replica_safe=true` for scaled production
deployments. If it does not, add Redis or reduce replica count.
