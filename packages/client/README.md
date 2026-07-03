# nodyra-client

Python SDK and `nodyra` CLI for [Nodyra](https://github.com/Harshit-repo/nodyra),
the Python-native, self-hostable workflow automation platform.

```bash
pip install nodyra-client
```

## CLI quickstart

```bash
nodyra login --base-url https://nodyra.example.com
nodyra workflow list
nodyra run start <workflow-id> --data '{"city": "Berlin"}' --watch
nodyra export script <workflow-id> -o flow.py
nodyra workflow import flow.module.py --name "Restored flow"
```

- `--json` on any command, or `NODYRA_JSON=1`, emits machine-readable output.
- `run start --watch` and `run watch <run-id>` follow a run live. Exit code is
  `0` for success, `2` for error, and `3` for cancelled, so CI can script
  against the run outcome.

## CI usage

```bash
export NODYRA_BASE_URL=https://nodyra.example.com
export NODYRA_TOKEN=$NODYRA_CI_TOKEN
nodyra --json run start "$WORKFLOW_ID" --watch
```

For non-interactive login:

```bash
echo "$TOKEN" | nodyra login --base-url "$NODYRA_BASE_URL" --token-stdin
```

## SDK quickstart

```python
from nodyra_client import NodyraClient

with NodyraClient(base_url="https://nodyra.example.com", token="ndpat_...") as client:
    workflow = client.workflows.create(name="My automation")
    run = client.runs.start(workflow.id, data={"key": "value"})
    for snapshot in client.runs.watch(run.id):
        print(snapshot.status)
```

Responses are typed pydantic models such as `WorkflowDetail` and `RunDetail`.
API failures raise `NodyraError(status, detail, hint)`.

## Configuration

| Source | Precedence |
| --- | --- |
| Explicit `NodyraClient(base_url=, token=)` or CLI flags | highest |
| `NODYRA_BASE_URL` and `NODYRA_TOKEN` environment variables | middle |
| `~/.nodyra/token` written by `nodyra login` with best-effort `0600` permissions | lowest |
