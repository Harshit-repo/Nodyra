# Getting Started with Nodyra

Nodyra is a self-hostable, Python-native workflow automation platform. This
guide takes you from zero to a running instance with your first workflow in
about ten minutes.

## 1. Requirements

- **Docker route (recommended):** Docker Engine or Docker Desktop with Compose.
- **Local dev route:** Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js
  20+, and Docker for Postgres/Redis/MinIO.

## 2. Start Nodyra with Docker Compose

Create `deploy/.env` with strong local secrets:

```bash
INTERNAL_API_TOKEN=replace-with-a-long-random-token
NODYRA_SECRET_KEY=replace-with-a-long-random-secret
AUTH_REQUIRED=true
AUTH_ALLOW_REGISTRATION=false
```

Start the stack:

```bash
docker compose -f deploy/docker-compose.yml up --build -d
```

Open <http://localhost:5173>. The stack includes PostgreSQL, Redis, MinIO, the
FastAPI control plane, a dispatch worker, and the web app. Verify health:

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

## 4. Build your first workflow

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

## 5. Seed the demo workspace

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

## 6. Let an AI agent build workflows for you

Nodyra ships a first-class MCP server with 61 tools. Connect Claude Code:

```bash
claude mcp add --transport http nodyra https://your-instance/mcp \
  --header "Authorization: Bearer <api-token>"
```

Then describe the workflow you want; it appears on the canvas — editable,
testable, and deployable. Full guide: [MCP quickstart](mcp-quickstart.md).

## 7. Go to production

- **Publish** your workflow to create an immutable version, then create a
  **deployment** pinned to that version. Drafts never affect production.
- Add **schedule** or **webhook** triggers to the deployment.
- Pick an execution posture (see [SECURITY.md](../SECURITY.md)): trusted
  single-tenant (default), sandboxed (`EXECUTION_SANDBOX=auto|required` —
  recommended when workflows run AI-generated code), or multi-tenant.
- Read the [deployment guide](deployment.md) for Helm, scaling workers, and
  the production checklist, and [backup & restore](backup-restore.md) before
  you rely on it.

## 8. Next steps

- [Architecture](architecture.md) — how the control plane, runtime pool, and
  environments fit together.
- [Working with datasets](datasetref.md) — DataFrame-scale data via
  artifact-backed DatasetRef handles and DuckDB SQL.
- [CLI & Python SDK](../packages/client/README.md) — `pip install
  nodyra-client`, run workflows from CI.
- [GitOps](gitops.md) — two-way GitHub sync for workflow definitions.
- [Licensing guide](licensing.md) — what's free, what's paid, in plain
  English.
