# Getting Started with Nodyra

Nodyra is a self-hostable, Python-native workflow automation platform. This
guide targets first value in about five minutes once container images are
available. A first local build can take longer depending on network and CPU.

## 1. Requirements

- **Docker route (recommended):** Docker Engine or Docker Desktop with Compose
  **2.24.4 or newer**.
- **Local dev route:** Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js
  20+, and Docker for Postgres/Redis/MinIO.

## 2. Start Nodyra with Docker Compose

Download the source archive from [release 1.0.5](https://github.com/Harshit-repo/Nodyra/releases/tag/v1.0.5),
or clone the release tag. This beta builds its container images locally.

```bash
git clone --branch v1.0.5 --depth 1 https://github.com/Harshit-repo/Nodyra.git
cd Nodyra
```

Copy the example environment file (PowerShell: `Copy-Item deploy/.env.example deploy/.env`):

```bash
cp deploy/.env.example deploy/.env
```

Open `deploy/.env` and replace all three active `CHANGE-ME` values and
`POSTGRES_PASSWORD` with independently generated secrets. For example, run
`openssl rand -hex 32` separately for each secret. Shell expressions such as
`$(openssl ...)` are not expanded inside an environment file.

Do not hand-write `deploy/.env` from scratch: `docker compose` hard-requires
`MINIO_ROOT_PASSWORD` as well, and fails before starting anything if it is
missing. Copying the example is the supported path.

For the first visit over loopback HTTP, set these values in `deploy/.env`:

```dotenv
SESSION_COOKIE_SECURE=false
CORS_ORIGINS=http://localhost:5173
```

Start the supported single-host stack with local artifact storage:

```bash
docker compose -p nodyra -f deploy/docker-compose.yml -f deploy/docker-compose.local-storage.yml up --build -d --wait
```

Open <http://localhost:5173>. The stack includes PostgreSQL, Redis, the
FastAPI control plane, a dispatch worker, and the web app. Artifacts persist in
a Docker volume. Configure HTTPS and restore `SESSION_COOKIE_SECURE=true`
before allowing access from another machine; follow the
[single-host guide](deployment/self-hosted.md) for ingress and backups.
Verify health:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

> Startup is fail-closed: if you keep a default `SECRET_KEY` with auth enabled
> or omit `INTERNAL_API_TOKEN` in a split topology, the server refuses to boot
> rather than run insecurely.

## 3. Create the owner account

The first registered account becomes the `owner`. Registration closes
automatically after the first user unless you explicitly enable it
(`AUTH_ALLOW_REGISTRATION=true`).

## 4. Run a verified starter template

1. Open **Workflows** and choose **Create from template**.
2. Select **Dataset filter and CSV export**. It needs no external credential.
3. Create the workflow, press **Run**, and open the execution when it finishes.
4. Inspect node input/output, then open **Artifacts** to see the CSV and its
   run/workflow lineage.

That completes the activation path without asking for a provider account.

## 5. Build a workflow from scratch

1. Click **New workflow** and drag a **Manual Trigger** onto the canvas.
2. Add an **HTTP Request** node, point it at an API you like, and wire it to
   the trigger.
3. Add a **Code** node and transform the response with plain Python — the
   input arrives as the first function argument.
4. Press **Run**. Click any node to inspect its exact input, output, logs, and
   timing.

Every node in Nodyra is a plain Python function. To write your own:

```python
from nodyra.sdk import node

@node(name="Normalize Customer", id="normalize_customer", category="Data")
def normalize_customer(input: dict, lowercase_email: bool = True) -> dict:
    customer = dict(input)
    if lowercase_email and customer.get("email"):
        customer["email"] = customer["email"].lower()
    return customer
```

Upload the module and it appears in the palette. See
[Writing a node](nodes.md).

## 6. Seed the demo workspace

For a populated local instance with demo workflows, a fake credential, a
runnable webhook, and one green MCP-created run:

```bash
make demo
```

The first run creates `.tmp/nodyra-demo.env` with strong random credentials;
later runs reuse that file so the PostgreSQL and MinIO volumes remain
accessible. Secret values are never printed. The demo disables authentication,
so every published port is deliberately bound to `127.0.0.1` and the generator
refuses a non-loopback bind. Use the authenticated deployment path above—not
`make demo`—for access from another machine.

The seeded webhook is active at `POST http://localhost:8000/webhook/demo/intake`:

```bash
curl -X POST http://localhost:8000/webhook/demo/intake \
  -H "Content-Type: application/json" \
  -d '{"customer":"Ada","event":"trial_started"}'
```

The fake credential is named `Demo Fake API Key` and is intentionally not valid
for any real service.

Stop the demo without deleting its data or credentials:

```bash
make demo-down
```

To rotate the local demo credentials, first stop the stack, then run:

```bash
uv run python scripts/generate_demo_env.py \
  --output .tmp/nodyra-demo.env --force
make demo
```

Rotating the PostgreSQL password does not rewrite an existing database volume's
role password. Remove the demo volumes as well if you intentionally want a
completely fresh demo database.

## 7. Let an AI agent build workflows for you

Nodyra ships an MCP server for creating and running workflows. Connect Claude Code:

```bash
claude mcp add --transport http nodyra https://your-instance/mcp \
  --header "Authorization: Bearer <api-token>"
```

Then describe the workflow you want; it appears on the canvas — editable,
testable, and deployable. Full guide: [MCP quickstart](mcp-quickstart.md).

## 8. Go to production

- **Publish** your workflow to create an immutable version, then create a
  **deployment** pinned to that version. Drafts never affect production.
- Add **schedule** or **webhook** triggers to the deployment.
- Review the execution trust boundary in [SECURITY.md](../SECURITY.md).
  This beta targets trusted workflow authors on one host. For a fail-closed
  sandbox, configure a supported sandbox backend and set
  `EXECUTION_SANDBOX=required`; `auto` can fall back to local execution.
- Read the [deployment guide](deployment.md) for Helm, scaling workers, and
  the production checklist, and [backup & restore](backup-restore.md) before
  you rely on it.

## 9. Next steps

- [Architecture](architecture.md) — how the control plane, runtime pool, and
  environments fit together.
- [Architecture chooser](architecture-chooser.md) — select a topology from
  trust and reliability requirements.
- [Migration](migration.md) and [recipes](recipes.md) — import compatibility
  reports and copy-paste starting points.
- [Working with datasets](datasetref.md) — DataFrame-scale data via
  artifact-backed DatasetRef handles and DuckDB SQL.
- [CLI & Python SDK](../packages/client/README.md) — install the current client
  from a source checkout and run workflows from CI.
- [GitOps](gitops.md) — two-way GitHub sync for workflow definitions.
- [Licensing guide](licensing.md) — what's free, what's paid, in plain
  English.
