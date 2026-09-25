# Single-host installation for trusted teams

Use this profile when one Docker host runs Nodyra for trusted workflow authors.
It stores artifacts on a persistent Docker volume and does not start MinIO or
require an external storage account. It is not a multi-tenant isolation or
high-availability configuration.

## Install

Install Docker Engine or Docker Desktop with Compose **2.24.4 or newer**.
Download the reviewed release source, then run from its root directory:

```sh
cp deploy/.env.example deploy/.env
```

Replace the three active `CHANGE-ME` values with independently generated random
secrets (`openssl rand -hex 32` for each). Keep the generated MinIO password in
the file: Compose validates the base file before applying the overlay, although
this profile never starts MinIO. Also set a strong `POSTGRES_PASSWORD` before the
first installation on a shared host. Keep `deploy/.env` private and backed up.

```sh
docker compose -p nodyra -f deploy/docker-compose.yml \
  -f deploy/docker-compose.local-storage.yml up --build -d
```

Open `http://localhost:5173` and create the owner account. Check
`http://localhost:8000/health/ready`, then run the **Dataset filter and CSV
export** template and download its artifacts. Registration closes after the
first account. The UI/API/database/cache ports are bound to loopback.

Long-running services use `restart: unless-stopped`. Docker restarts them after
a daemon restart unless an operator explicitly stopped them. Compose waits for
database and API health during initial startup.

## HTTPS access from other machines

Put a TLS reverse proxy in front of the web port. For a host-installed Caddy:

```caddyfile
nodyra.your-domain.example {
    reverse_proxy 127.0.0.1:5173
}
```

Use your owned domain and configure its DNS and ports 80/443. Before starting
the application, set these values in `deploy/.env`:

```dotenv
CORS_ORIGINS=https://nodyra.your-domain.example
PUBLIC_API_URL=https://nodyra.your-domain.example/api
TRUSTED_PROXY_COUNT=2
SESSION_COOKIE_SECURE=true
```

The proxy count represents TLS proxy → web nginx → API. Adjust it to the real
topology. Keep the API, database and cache off public interfaces. Complete owner
setup before granting network access. A local test certificate verifies the
proxy configuration; it does not replace a publicly trusted certificate.

## Execution and storage boundary

The base `EXECUTION_SANDBOX=auto` setting falls back to subprocess execution
when Docker is unavailable to the worker. Authors must therefore be trusted
to run Python on this host. For isolated execution, apply the sandbox overlay
last, and follow the [sandbox configuration](../deployment.md#sandboxed-execution).
Do not enable multi-tenancy without its required isolation controls.

Artifacts live in `nodyra_artifactdata`, environments in `nodyra_envdata`, and
PostgreSQL in `nodyra_pgdata` when the project name above is used. Retain these
volumes during upgrades. Back up a consistent database dump, artifacts,
environment definitions and secrets outside the host, and verify a restore
using the [backup and restore guide](../backup-restore.md).

Local-storage warnings in the operational diagnostics are expected for this
single-host profile: the volume is persistent, but losing that host still loses
unbacked-up data. Multi-host workers need appropriately shared storage or the
external-S3 profile. Do not switch an existing S3 installation to local storage
as a substitute for migrating its previously stored objects.
