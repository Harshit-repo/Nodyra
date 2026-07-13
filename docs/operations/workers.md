# Worker Operations

Nodyra workers execute trusted local runs, sandboxed container-per-run jobs, and
remote runner-pool assignments. Treat Docker daemon access as privileged
infrastructure: a process that can create arbitrary containers can usually
control the host.

## Security Tiers

| Tier | Use case | Docker access | Tenant posture |
| --- | --- | --- | --- |
| Subprocess worker | Single-tenant, trusted authors | None | Trusted code only |
| Socket-proxy sandbox worker | Self-hosted teams that want container-per-run isolation | `docker-socket-proxy` with endpoint allow-list | Stronger isolation, still same host daemon |
| Rootless daemon / Podman | Multi-team deployments that need less host blast radius | `SANDBOX_DOCKER_HOST=unix:///run/user/<uid>/podman/podman.sock` or rootless Docker | Preferred for untrusted code on a single host |
| Kubernetes / remote runner pool | Hard multi-tenant or regulated isolation | No host socket in the app worker | Preferred production boundary |

## Compose Sandbox Worker

Start sandbox execution with:

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.sandbox.yml up -d
```

The sandbox overlay starts `docker-socket-proxy` and sets:

```env
EXECUTION_SANDBOX=auto
SANDBOX_RUNTIME=auto
SANDBOX_DOCKER_HOST=tcp://docker-socket-proxy:2375
```

The worker service must not mount `/var/run/docker.sock`. The only service that
mounts the host socket is `docker-socket-proxy`, and it mounts the socket
read-only. The proxy allow-list enables the Docker API groups needed by sandbox
runs:

- `BUILD`, `IMAGES`, `NETWORKS`, `CONTAINERS`, `INFO`, `VERSION`
- `POST`, required for create/start/stop/remove/build operations

The overlay disables higher-risk groups that are not needed by sandbox runs:
`EXEC`, `VOLUMES`, `SECRETS`, `SERVICES`, `SWARM`, `PLUGINS`, `AUTH`, and
related cluster/admin endpoints.

## Verification

Render the compose config and confirm the worker has no raw socket bind:

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.sandbox.yml config
```

Then inspect the worker container:

```bash
docker inspect nodyra-worker-1 --format '{{json .Mounts}}' | jq
```

The worker should have `envdata` and `artifactdata` mounts, but no
`/var/run/docker.sock`. The proxy container should have the socket mount.

Run a sandbox smoke after the stack is healthy:

```bash
EXECUTION_SANDBOX=required docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.sandbox.yml up -d
```

Create a workflow with a code node, run it, and confirm `/ops/sandbox` reports
the selected runtime and network. A proxy-denied verb such as Docker exec should
fail through the proxy; sandbox runs do not require it.

## Rootless Alternative

For stronger single-host isolation, run a rootless Docker or Podman daemon and
point workers at that daemon instead of the compose proxy:

```bash
systemctl --user enable --now podman.socket
export SANDBOX_DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock
export EXECUTION_SANDBOX=required
```

Rootless Podman may ignore `SANDBOX_RUNTIME=runsc|kata`; verify the runtime
reported by `/ops/sandbox` and keep resource limits enabled with cgroups v2.
