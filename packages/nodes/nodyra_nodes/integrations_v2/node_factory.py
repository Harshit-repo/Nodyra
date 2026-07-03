"""Generate Python-native node definitions from operation specs."""

from __future__ import annotations

import inspect
import keyword
import re
import textwrap
from collections.abc import Callable
from typing import Any

from nodyra.models import (
    IntegrationManifest,
    IntegrationOperationManifest,
    IntegrationResourceManifest,
    NodeManifest,
    NodeRole,
    ParamSpec,
    PortDataKind,
    PortSpec,
)
from nodyra.sdk import NodeDef
from nodyra_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ProviderTriggerSpec,
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_identifier(value: str) -> str:
    candidate = re.sub(r"\W+", "_", value).strip("_")
    if not candidate:
        candidate = "operation"
    if candidate[0].isdigit():
        candidate = f"_{candidate}"
    if keyword.iskeyword(candidate):
        candidate = f"{candidate}_"
    return candidate


def _param_source(param: OperationParamSpec) -> str:
    name = _safe_identifier(param.name)
    if name != param.name or not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Operation parameter '{param.name}' is not a Python identifier")
    default = "None" if param.required and param.default is None else repr(param.default)
    return f"{name}={default}"


def operation_manifest(spec: OperationSpec) -> NodeManifest:
    usable_as_tool = (
        spec.role == NodeRole.executable
        if spec.usable_as_tool is None
        else bool(spec.usable_as_tool)
    )
    return NodeManifest(
        id=spec.node_id,
        name=spec.name,
        category=spec.category,
        version=spec.version,
        description=spec.description,
        icon=spec.icon,
        role=spec.role,
        usable_as_tool=usable_as_tool,
        tool_side_effecting=spec.tool_side_effecting,
        inputs=[
            PortSpec(
                name="input",
                description="Upstream item/data input.",
                data_kind=spec.input_kind,
            )
        ],
        params=[param.to_param_spec() for param in spec.params],
        outputs=[
            PortSpec(
                name="main",
                description="Operation result.",
                data_kind=spec.output_kind,
            )
        ],
        requirements=list(spec.requirements),
    )


def operation_source(spec: OperationSpec) -> str:
    function_name = _safe_identifier(spec.node_id)
    params = ["input=None", *[_param_source(param) for param in spec.params]]
    call_args = ["input=input", *[f"{param.name}={param.name}" for param in spec.params]]
    return textwrap.dedent(
        f'''
        def {function_name}({", ".join(params)}):
            """{spec.name}: {spec.provider}/{spec.resource}/{spec.operation}.

            Generated from an OperationSpec. The node remains ordinary Python:
            parameters are explicit, credentials are resolved before execution,
            and the provider call is delegated to the registered operation
            executor for {spec.operation_key}.
            """
            from nodyra_nodes.integrations_v2.registry import execute_registered_operation

            return execute_registered_operation(
                "{spec.node_id}",
                {", ".join(call_args)},
            )
        '''
    ).strip()


def make_operation_node_def(
    spec: OperationSpec,
    executor: Callable[..., Any],
) -> NodeDef:
    source = operation_source(spec)
    namespace: dict[str, Any] = {}
    exec(source, namespace)  # noqa: S102 - generated trusted node source
    function_name = _safe_identifier(spec.node_id)
    func = namespace[function_name]
    func.__doc__ = inspect.cleandoc(func.__doc__ or "")
    func.__nodyra_source__ = source
    func.__nodyra_operation_spec__ = spec
    func.__nodyra_operation_executor__ = executor
    return NodeDef(
        func=func,
        manifest=operation_manifest(spec),
        is_async=False,
        param_names=frozenset(["input", *[param.name for param in spec.params]]),
        accepts_var_keyword=False,
        declared_id=spec.node_id,
    )


def trigger_manifest(spec: ProviderTriggerSpec) -> NodeManifest:
    return NodeManifest(
        id=spec.node_id,
        name=spec.name,
        category=spec.category,
        version=spec.version,
        description=spec.description,
        icon=spec.icon,
        role=NodeRole.trigger,
        inputs=[],
        params=[param.to_param_spec() for param in spec.params],
        outputs=[
            PortSpec(
                name="main",
                description="Provider event payload.",
                data_kind=spec.output_kind,
            )
        ],
        requirements=list(spec.requirements),
    )


def trigger_source(spec: ProviderTriggerSpec) -> str:
    function_name = _safe_identifier(spec.node_id)
    params = [_param_source(param) for param in spec.params]
    return textwrap.dedent(
        f'''
        def {function_name}({", ".join(params)}):
            """{spec.name}: {spec.provider}/{spec.resource}/{spec.event}.

            Generated from a ProviderTriggerSpec. Production events arrive via
            the provider webhook ingress and are injected as this trigger node's
            output; direct execution returns an empty sample payload.
            """
            return {{}}
        '''
    ).strip()


def make_provider_trigger_node_def(spec: ProviderTriggerSpec) -> NodeDef:
    source = trigger_source(spec)
    namespace: dict[str, Any] = {}
    exec(source, namespace)  # noqa: S102 - generated trusted node source
    function_name = _safe_identifier(spec.node_id)
    func = namespace[function_name]
    func.__doc__ = inspect.cleandoc(func.__doc__ or "")
    func.__nodyra_source__ = source
    func.__nodyra_provider_trigger_spec__ = spec
    return NodeDef(
        func=func,
        manifest=trigger_manifest(spec),
        is_async=False,
        param_names=frozenset(param.name for param in spec.params),
        accepts_var_keyword=False,
        declared_id=spec.node_id,
    )


# ---------------------------------------------------------------------------
# Consolidated integration nodes (n8n-style Resource + Operation)
# ---------------------------------------------------------------------------


def _humanize(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _operation_label(spec: IntegrationSpec, op: OperationSpec) -> str:
    """Drop the provider prefix so the Operation dropdown reads cleanly.

    e.g. "Slack Send Message" → "Send Message", "Google Sheets Append" → "Append".
    """
    prefix = f"{spec.name} "
    label = op.name[len(prefix):] if op.name.startswith(prefix) else op.name
    return label or _humanize(op.operation)


def _display_when(conditions: list[tuple[str, str]]) -> dict[str, Any]:
    """Build a display_when matching the (resource, operation) pairs a param
    belongs to. One pair → a single AND group; several → an OR of AND groups."""
    groups = [
        {
            "conditions": [
                {"param": "resource", "value": resource},
                {"param": "operation", "value": operation},
            ]
        }
        for resource, operation in conditions
    ]
    return groups[0] if len(groups) == 1 else {"any": groups}


def _integration_params(spec: IntegrationSpec) -> list[ParamSpec]:
    """Resource/operation selectors + one shared credential + the deduped union
    of every operation's params, each gated by its (resource, operation) pairs."""
    first_resource = spec.resources[0]
    resource_param = ParamSpec(
        name="resource",
        type="string",
        widget="hidden",
        default=first_resource.id,
        choices=[resource.id for resource in spec.resources],
    )
    operation_param = ParamSpec(
        name="operation",
        type="string",
        widget="hidden",
        default=first_resource.operations[0].operation,
        choices=[op.operation for op in spec.operations()],
    )

    credential_param: ParamSpec | None = None
    union: dict[str, ParamSpec] = {}
    conditions: dict[str, list[tuple[str, str]]] = {}
    for resource in spec.resources:
        for op in resource.operations:
            for param in op.params:
                if param.type == "credential":
                    if credential_param is None:
                        # Shared across operations → always visible (no display_when).
                        credential_param = param.to_param_spec()
                    continue
                if param.name not in union:
                    union[param.name] = param.to_param_spec()
                    conditions[param.name] = []
                conditions[param.name].append((resource.id, op.operation))

    params: list[ParamSpec] = [resource_param, operation_param]
    if credential_param is not None:
        params.append(credential_param)
    for name, base in union.items():
        base.display_when = _display_when(conditions[name])
        params.append(base)
    return params


def _integration_descriptor(spec: IntegrationSpec) -> IntegrationManifest:
    return IntegrationManifest(
        provider=spec.id,
        resources=[
            IntegrationResourceManifest(
                id=resource.id,
                name=resource.name,
                operations=[
                    IntegrationOperationManifest(
                        id=op.operation,
                        name=_operation_label(spec, op),
                        description=op.description,
                    )
                    for op in resource.operations
                ],
            )
            for resource in spec.resources
        ],
    )


def integration_manifest(spec: IntegrationSpec) -> NodeManifest:
    return NodeManifest(
        id=spec.id,
        name=spec.name,
        category=spec.category,
        version=spec.version,
        description=spec.description,
        icon=spec.icon,
        role=NodeRole.executable,
        usable_as_tool=True,
        tool_side_effecting=True,
        inputs=[
            PortSpec(
                name="input",
                description="Upstream item/data input.",
                data_kind=PortDataKind.main,
            )
        ],
        params=_integration_params(spec),
        outputs=[
            PortSpec(
                name="main",
                description="Operation result.",
                data_kind=PortDataKind.main,
            )
        ],
        integration=_integration_descriptor(spec),
    )


def integration_source(spec: IntegrationSpec, param_specs: list[ParamSpec]) -> str:
    function_name = _safe_identifier(spec.id)
    signature = ["input=None"]
    forwarded = ["input=input"]
    for param in param_specs:
        ident = _safe_identifier(param.name)
        if ident != param.name or not _IDENTIFIER_RE.match(ident):
            raise ValueError(f"Integration parameter '{param.name}' is not a Python identifier")
        default = "None" if param.required and param.default is None else repr(param.default)
        signature.append(f"{ident}={default}")
        # resource/operation are passed positionally to the dispatcher below.
        if param.name not in ("resource", "operation"):
            forwarded.append(f"{param.name}={param.name}")
    body_call = ",\n                ".join(forwarded)
    return textwrap.dedent(
        f'''
        def {function_name}({", ".join(signature)}):
            """{spec.name}: consolidated integration node.

            Generated from an IntegrationSpec. The selected ``resource`` and
            ``operation`` choose which provider operation runs; the dispatcher
            forwards only that operation's declared params to its executor.
            """
            from nodyra_nodes.integrations_v2.registry import execute_integration_operation

            return execute_integration_operation(
                "{spec.id}",
                resource,
                operation,
                {body_call},
            )
        '''
    ).strip()


def make_integration_node_def(spec: IntegrationSpec) -> NodeDef:
    if not spec.resources or not spec.resources[0].operations:
        raise ValueError(f"Integration '{spec.id}' must declare at least one operation")
    manifest = integration_manifest(spec)
    param_specs = manifest.params
    source = integration_source(spec, param_specs)
    namespace: dict[str, Any] = {}
    exec(source, namespace)  # noqa: S102 - generated trusted node source
    function_name = _safe_identifier(spec.id)
    func = namespace[function_name]
    func.__doc__ = inspect.cleandoc(func.__doc__ or "")
    func.__nodyra_source__ = source
    func.__nodyra_integration_spec__ = spec
    return NodeDef(
        func=func,
        manifest=manifest,
        is_async=False,
        param_names=frozenset(["input", *[param.name for param in param_specs]]),
        accepts_var_keyword=False,
        declared_id=spec.id,
    )
