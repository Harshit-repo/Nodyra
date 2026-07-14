# Scaling Workers

Nodyra has two kinds of execution capacity, designed for different trust
levels. Both can be added with one command.

| | Dispatch worker | Agent runner |
|---|---|---|
| Binary | `python -m app.worker_main` (same image as the API) | `nodyra-runner` (`packages/runner`, pip-installable) |
| Trust | Part of the platform (direct Postgres + Redis access) | Untrusted-network friendly: outbound WebSocket only |
| Work it takes | Local subprocess/sandbox runs + `docker` runner-pool entries, leased from the durable queue (`SELECT … FOR UPDATE SKIP LOCKED`) | Runs assigned to its **agent runner pool** over the WS |
| Add one | `docker compose up -d --scale worker=3` | UI → Runner Pools → Add Runner → copy the join command |
| Remove one | `docker compose up -d --scale worker=2` (SIGTERM drains) | Drain button in UI, then delete the runner |

---

## 1. Dispatch workers (Docker Compose)

The compose file in `deploy/` already separates the control plane (`api`,
`DISPATCH_ROLE=control`) from execution (`worker`, `DISPATCH_ROLE=worker`).
Scaling is one command:

```bash
cd deploy
docker compose up -d --scale worker=3
```

That is the whole procedure. Each worker:

- **leases work safely** — the durable run queue hands each entry to exactly
  one worker (Postgres row locks + lease expiry); adding workers can never
  double-execute a run;
- **heartbeats** — an active run's lease is extended while it executes; if a
  worker dies, the lease expires and the run is requeued for a peer (or
  dead-lettered after `QUEUE_DEFAULT_MAX_ATTEMPTS`);
- **registers a replica heartbeat** in Redis — `GET /ops/replicas` (and the
  Settings → Replicas card) shows every live API/worker process, its role and
  last-seen time;
- **drains on SIGTERM** — stops leasing, waits
  `QUEUE_DISPATCH_SHUTDOWN_TIMEOUT_SECONDS` for in-flight runs, then cancels
  laggards (they are recovered by lease expiry);
- **serves health + metrics** when configured (see below).

### Required environment (already set in `deploy/docker-compose.yml`)

| Variable | Why |
|---|---|
| `DISPATCH_ROLE=worker` | Enables the worker entrypoint; startup **fails closed** if Redis/Postgres are missing. |
| `QUEUE_BACKEND=redis` | Run events must reach API replicas across processes. |
| `DATABASE_URL` (Postgres) | SKIP LOCKED leasing needs Postgres. |
| `INTERNAL_API_TOKEN` | Shared API↔worker secret; startup refuses a blank value in split topology. |
| `SECRET_KEY` | Same value as the API — workers decrypt credentials with the same KEK chain. |

### Worker health + metrics endpoints

```bash
WORKER_HEALTH_PORT=9402    # GET /health/live, /health/ready (DB+Redis+sandbox)
WORKER_METRICS_PORT=9401   # GET /metrics (OpenMetrics for Prometheus)
```

Either port serves all three paths; set them equal to run one listener. The
Helm chart's `worker.healthPort` value wires `WORKER_HEALTH_PORT` and a
readiness probe automatically. Firewall these ports to your monitoring
network — they are unauthenticated (they return no secrets, but queue/run
counts are still internal data).

### Sandboxed execution on workers

Add the sandbox overlay so workers run workflow code in disposable hardened
containers instead of the warm subprocess pool:

```bash
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up -d --scale worker=3
```

The worker reaches the host daemon through the sandbox overlay's restricted
Docker socket proxy and spawns **sibling** run containers (Docker-out-of-Docker)
on the isolated `nodyra-sandbox` bridge network — the worker process itself
never executes untrusted code. `SANDBOX_RUNTIME=auto` picks the strongest
available runtime (kata > gVisor/runsc > runc). `EXECUTION_SANDBOX=required`
refuses worker startup without a working daemon — mandatory when
`MULTI_TENANCY_ENABLED=true`.

Each run container is labeled with its worker owner. On process restart the
worker force-removes containers left by its prior process before leasing new
work. `SANDBOX_OWNER_ID` defaults to the worker hostname; set it to a stable,
unique value per replica when an orchestrator recreates workers on a shared
Docker daemon. Never reuse one owner ID across concurrently active replicas.

### Artifacts across workers

- **Single host:** the shared `artifactdata` volume (compose default) is
  enough — API and all workers see the same files.
- **Multiple hosts / Kubernetes:** set `ARTIFACT_STORAGE_BACKEND=s3` (MinIO,
  R2, B2 or AWS) so artifacts are shared through the bucket instead of a
  local disk. The compose file already ships a MinIO service.

### Kubernetes

```bash
helm upgrade nodyra deploy/helm/nodyra --set worker.replicas=5
```

The chart runs the worker as its own Deployment with a `pgrep` liveness probe
and, when `worker.healthPort > 0`, an HTTP readiness probe against the
worker's `/health/ready`. Migrations run once via the chart's migration Job —
API and workers must always deploy the same migration head.

---

## 2. Agent runners (remote machines, GPU boxes, on-prem)

Agent runners connect **outbound** over WebSocket, so they work behind NAT
and need no inbound firewall holes. Use them for capacity the platform host
can't provide: GPU machines, high-memory boxes, machines inside a customer
network.

### Join a machine (UI)

1. **Runner Pools → New Pool** (provider: *agent*).
2. **Add Runner** → optionally set name, max concurrent runs, capability
   labels (e.g. `{"gpu": true, "python": "3.12"}`).
3. Copy the generated join command:

```bash
pip install nodyra-runner
nodyra-runner register --api-url https://nodyra.example.com --token <TOKEN>
nodyra-runner start
```

Or let Nodyra do it: **SSH onboard** takes host + credentials, installs the
agent, registers it, and starts it (optionally as a systemd unit). SSH
credentials are stored encrypted so the machine can be restarted from the UI.

### Token + identity model

- The join token is a **signed, org-scoped credential** bound to one
  pre-created runner row (`sub` = runner id, plus pool + org claims).
- It is reusable across agent restarts, expires after
  `RUNNER_TOKEN_TTL_DAYS` (default 365), and is **revoked instantly** by
  deleting the runner row.
- The WS handshake re-verifies the token signature, runner/pool/org binding;
  artifact uploads re-verify it per request.

### Lifecycle you get for free

| Concern | Behaviour |
|---|---|
| Heartbeat | Server pings every `RUNNER_HEARTBEAT_INTERVAL_SECONDS` (15s); no pong for `RUNNER_OFFLINE_AFTER_SECONDS` (60s) → runner marked offline, its in-flight runs requeued. |
| Capabilities | Agent advertises `max_concurrent` + cached env ids on hello; capability labels stored on the runner row and used for label-based dispatch. |
| Env caching | Runners rebuild each workflow environment once and advertise the cache, so repeat runs skip pip installs. |
| Drain | `POST /runner-pools/{pool}/runners/{id}/drain` (UI button) — finish in-flight runs, accept nothing new. |
| Restart | SSH-onboarded runners can be restarted from the UI. |
| Ghost cleanup | Runners that never connected are auto-deleted after `RUNNER_GHOST_TTL_HOURS` (48h). |
| Health dashboard | `GET /runner-pools/health` + the Runner Pools page: per-runner status, last-seen, current/max runs, run history buckets. |

### What agent runners can reach

Agents never receive DB or Redis credentials. The API resolves the workflow's
environment + credentials into the run payload it sends over the WS, and the
runner uploads artifacts back over HTTPS using its runner token. Compromising
an agent machine exposes only the runs routed to it — scope pools accordingly
(e.g. a dedicated pool per customer network).

### One-click Docker runners

Instead of manually installing and joining agents on remote machines, create
long-lived Docker-backed agent runners from the UI:

1. **Runner Pools → Pool (agent)** → **Add Docker Runner** button.
2. Nodyra spawns a `nodyra-runner` container on the configured Docker daemon
   (local or remote via `docker_host: tcp://...`) with resource limits you set
   (CPU, memory, pids limit) and restarts policy.
3. The container registers itself and joins the pool automatically.

#### Autoscaling Docker runners

Optionally enable autoscaling in the pool config:

- **Bounds:** min/max runners (max hard-capped at 32), idle scale-down timeout.
- **Queue monitoring:** the autoscaler ticks every
  `DOCKER_AUTOSCALE_TICK_SECONDS` (default 30s), snapshots the queued runs,
  and scales up one runner when all online runners are saturated; scales down
  one idle runner after the timeout expires.
- **Self-healing:** each tick also reconciles the pool — a runner whose
  container has died is removed from the DB so it stops counting toward
  `max_runners` (otherwise dead rows would silently collapse pool capacity),
  and a container whose row is gone is stopped. Removal never deletes a runner
  row while its daemon is unreachable, so a transient blip can't orphan a live
  container.
- **Daemon choice:** target the local Docker daemon (default) or a remote host
  (`docker_host: tcp://host:2375` or `ssh://user@host`). Only `tcp://`,
  `ssh://`, `unix://` and `npipe://` schemes are accepted; resource requests are
  bounds-checked on save.

#### Sandbox checkbox (hardened per-run containers)

Check **Sandboxed execution support** to give spawned runners Docker daemon
access. Each workflow run then executes in a disposable hardened sibling
container (the runner's WebSocket connection remains untrusted):

- Runner container: standard process, has Docker socket.
- Run container: hardened (cap_drop ALL, no-new-privileges, read-only rootfs,
  tmpfs `/tmp`, non-root, cpu/mem/pids caps), on a dedicated
  `nodyra-agent-sandbox` bridge network (isolated from unrelated containers on
  the host but able to reach the API to upload artifacts), ephemeral, removed
  after the run completes.
- **Routing:** sandbox-required runs are only ever offered runners that
  advertise sandbox support (the capability is folded into the dispatch label
  filter), with a fail-closed guard as a second line of defence — so a mixed
  pool never runs a sandboxed workflow on a plain runner. Admission also
  requires the pool to be sandbox-configured; a plain agent pool still refuses
  sandboxed workflows.

**Trust note:** the runner has root-equivalent access to the daemon host
through the socket. Scope this pool to trusted operators only, or run it on
a dedicated machine per tenant.

---

## 3. Routing runs to the right capacity

- **Per-workflow runner pool:** a workflow (or deployment) pinned to a pool
  only executes there — use this for GPU/high-memory workloads.
- **Provider capability split:** dispatch workers lease `local` + `docker`
  entries; `agent`/`kubernetes` pool runs are dispatched by the API replica
  that terminates the runner WebSocket (`DISPATCH_ROLE=control`).
- **Org fairness:** with multi-tenancy on, the queue leases from the org with
  the fewest in-flight runs first and enforces per-org concurrency caps, so
  one tenant's burst cannot starve others.
- **Local admission control:** worker concurrency is bounded by
  `MAX_CONCURRENT_RUNS`, per-env pool sizes, and the optional RSS budget
  (`WORKER_RSS_SOFT_BUDGET_BYTES`) so heavy environments can't OOM the host.

## 4. Upgrading workers

1. Deploy/migrate the API first (same image tag everywhere; helm's migration
   Job runs `alembic upgrade head` once).
2. Roll workers: compose — `docker compose up -d --build worker`; helm — the
   worker Deployment rolls with `maxUnavailable: 0`.
3. SIGTERM drain means a rolling restart never kills runs silently: anything
   that can't finish inside the drain window is requeued via lease expiry.

Agent runners are versioned separately (`pip install -U nodyra-runner`);
drain the runner in the UI before upgrading it.
