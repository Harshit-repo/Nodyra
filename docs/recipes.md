# Copy-Paste Recipes

These recipes are intentionally small and inspectable. Start from the template
gallery when a matching verified template exists.

## Poll an API and normalize records

Create **Schedule Trigger → HTTP Request → Code**. Configure the request for a
public HTTPS endpoint, then use:

```python
rows = input if isinstance(input, list) else input.get("items", [])
output = [
    {"id": row.get("id"), "name": str(row.get("name", "")).strip()}
    for row in rows
    if row.get("id") is not None
]
```

Pin a representative HTTP response while authoring, add a timeout/retry policy,
and publish only after the output schema is stable.

## Filter a large dataset without loading it into memory

Create **Manual Trigger → Records To Dataset → Dataset Filter → CSV Write**.
Use the filter expression `region = 'west' AND amount >= 100`. Dataset nodes
pass artifact-backed `DatasetRef` handles so the workflow does not serialize a
large DataFrame through every edge.

## Turn a webhook into an operational alert

Create **Webhook Trigger → Code → Slack**. Keep inbound authentication enabled,
map only approved fields, and store the Slack token as a scoped credential.
Deploy a failure workflow for provider errors and alert on sustained failure
ratio rather than a single retry.

## Invoke from CI

```bash
python -m pip install ./packages/client
printf '%s\n' "$NODYRA_TOKEN" | \
  nodyra login --base-url "$NODYRA_URL" --token-stdin
nodyra run start "$WORKFLOW_ID" --data "$(cat build-payload.json)" --watch
```

The client is installed from a Nodyra source checkout until its first PyPI
release.

The command exits non-zero for failed or cancelled runs, making the workflow a
normal CI gate. Pin a published workflow version through a deployment for
production use; do not invoke an editable draft from release automation.
