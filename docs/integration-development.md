# Building V2 Integrations

V2 integrations keep Nodyra's core promise: every node is Python-native. The
node shown in the editor is generated from a spec, but the generated source is
still a normal Python function and the source viewer works.

Do not copy provider code from n8n. Use provider docs to define the HTTP
contract, then implement the operation in Python using the shared transport.

## Operation Node Checklist

1. Create or extend a provider package under
   `packages/nodes/nodyra_nodes/integrations_v2/providers/<provider>/`.
2. Define credentials with `CredentialSpec` inside an `OperationParamSpec`.
3. Define an `OperationSpec` with stable `node_id`, provider, resource,
   operation, params, icon, and documentation URL when available.
4. Set node-as-tool metadata deliberately:
   - executable operation nodes are usable as AI tools by default;
   - set `tool_side_effecting=False` for read-only list/get/search/metadata
     operations;
   - keep `tool_side_effecting=True` for create/update/delete/send/clear
     operations so agent calls use the approval gate;
   - set `usable_as_tool=False` only when an operation cannot safely be called
     by an AI agent.
5. Implement an executor function with explicit Python parameters. The executor
   should call `ProviderTransport` or a provider-specific transport subclass.
6. Register the operation with `register_operation(SPEC, executor)`.
7. Import the provider package from `packages/nodes/nodyra_nodes/__init__.py`
   so default startup registers the nodes.
8. Add mocked provider tests. Do not require real network calls.

Minimal shape:

```python
from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_operation
from nodyra_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec
from nodyra_nodes.integrations_v2.transport import ProviderTransport


MY_READ_SPEC = OperationSpec(
    node_id="my_provider_read_v2",
    name="My Provider Read V2",
    provider="my_provider",
    resource="record",
    operation="read",
    params=(
        OperationParamSpec(
            name="credentials",
            type="credential",
            required=True,
            credential=CredentialSpec(
                type="my_provider",
                key="*",
                label="My Provider credential",
                fields=["api_key"],
            ),
        ),
        OperationParamSpec(name="record_id", required=True),
    ),
)


def read_record(input=None, credentials=None, record_id=""):
    transport = ProviderTransport(
        provider="my_provider",
        base_url="https://api.example.com/v1",
        default_headers={"Authorization": f"Bearer {credentials['api_key']}"},
    )
    return transport.request(
        "GET",
        f"/records/{record_id}",
        operation="read_record",
    )


register_operation(MY_READ_SPEC, read_record)
```

## Provider Trigger Checklist

Provider triggers are different from regular webhook triggers. The API owns
activation/deactivation with the external provider, stores a durable
`ProviderTriggerSubscription`, and injects the received event into the trigger
node output.

1. Define a `ProviderTriggerSpec`.
2. Implement `activate(context)` to create the provider subscription using
   `context.callback_url`.
3. Implement `deactivate(context)` to delete the provider subscription when the
   workflow is deactivated or the trigger changes.
4. Implement `handle_event(request, params)` to verify signatures/challenges and
   return a `ProviderTriggerEvent`.
5. Set a stable `dedupe_key` for deliveries when the provider supplies one.
6. Register with `register_provider_trigger(SPEC)`.
7. Add tests for activation, deactivation, signature failure, ping/challenge
   acknowledgement, dispatch, duplicate delivery, status endpoint, and audit.

Trigger handlers must not store raw headers, raw bodies, signatures, or secret
params in subscription config or timeline metadata. Normalize only safe
metadata such as provider event name, delivery id, repository/file id, and
response status.

## Credentials

Credential references are resolved by the API before node execution. Nodes
should accept credentials as normal dictionaries and never call the database.

Use these patterns:

- `CredentialSpec(type="provider", key="*", fields=[...], multi=True)` for
  multi-field credentials.
- `CredentialSpec(type="provider", key="api_key", fields=["api_key"])` for
  single-field credentials.
- Keep provider secrets out of outputs, logs, timeline events, debug metadata,
  subscription config, and exception strings.
- Add or update credential test handlers in `apps/api/app/services/credential_tests.py`
  only when the test is read-only and safe to run on save.

Credential specs live with the node spec so the generated manifest can tell the
UI which credential type to pick. The credential `type` must match the backend
credential type id and the credential test service id.

```python
OperationParamSpec(
    name="credentials",
    type="credential",
    required=True,
    credential=CredentialSpec(
        type="my_provider",
        key="*",
        label="My Provider credential",
        fields=["api_key", "account_id"],
        multi=True,
    ),
)
```

For normal decorator-based nodes, keep using the SDK helpers such as
`cred_single(...)` and `cred_multi(...)`. V2 provider specs use
`CredentialSpec` directly because the generator turns the spec into a normal
Python node manifest.

Credential test handlers should prove that the credential can authenticate
without mutating provider data. Use a cheap read-only endpoint such as
`/me`, `/models`, `/account`, or a metadata endpoint. Never create, update,
delete, send, or publish data from a credential test.

Minimal backend test-handler shape:

```python
async def _test_my_provider(
    data: dict[str, str],
    context: dict[str, Any],
) -> dict[str, Any]:
    api_key = _value(data, "api_key", "token")
    if not api_key:
        return {"ok": False, "message": "Missing api_key", "details": {}}
    return await _request(
        "GET",
        "https://api.example.com/v1/me",
        headers={"Authorization": f"Bearer {api_key}"},
    )
```

Register the handler in `_TESTERS`:

```python
_TESTERS: dict[str, TestFn] = {
    "my_provider": _test_my_provider,
}
```

`test_credential_connection(...)` centrally records latency and redacts secret
values from the message and details before returning the result. Keep custom
handlers focused on connection diagnostics and safe, non-secret metadata such as
status code, provider account id, or account display name.

## Dynamic Options

Use `register_loader(...)` in `integrations_v2.dynamic_options` when a parameter
needs provider-loaded choices such as spreadsheet tabs or columns. Loaders must:

- use the same transport and credential shape as operations;
- return `DynamicOption` objects only;
- avoid storing provider responses;
- tolerate missing context by returning an empty list.

## Transport And Observability

Use `ProviderTransport` unless the provider needs a small subclass for auth or
provider-specific params.

The transport already provides:

- retries for 429/5xx responses;
- `Retry-After` and rate-limit reset handling;
- structured `ProviderError`;
- request id extraction;
- redacted `provider_requests` node debug events with attempt, latency,
  status, retryability, and retry scheduling.

Do not log headers, request bodies, query strings, response bodies, API keys, or
OAuth tokens.

## Required Tests

For each operation:

- manifest registration and source generation;
- node-as-tool manifest compatibility: `usable_as_tool` is true by default and
  `tool_side_effecting` matches the operation's read/write behavior;
- successful mocked provider call;
- provider error shape;
- credential redaction if any error/output could include credential-adjacent
  data;
- dynamic option loader tests when applicable.

For each trigger:

- manifest registration and source generation;
- activation creates provider subscription;
- deactivation deletes provider subscription;
- signature/challenge handling;
- duplicate delivery dedupe;
- workflow dispatch with trigger node cache;
- `/workflows/{id}/provider-triggers` status output;
- timeline metadata and audit rows.

Useful focused commands:

```powershell
uv run pytest packages/nodes/tests/test_integrations_v2_registry.py
uv run pytest packages/nodes/tests/test_integrations_v2_transport.py
uv run pytest apps/api/tests/test_triggers.py
uv run ruff check packages/nodes/nodyra_nodes/integrations_v2 apps/api/app/services/provider_triggers.py
```
