"""Generate Python-native node definitions from operation specs."""

from __future__ import annotations

import inspect
import keyword
import re
import textwrap
from collections.abc import Callable
from typing import Any

from noodle.models import NodeManifest, NodeRole, PortSpec
from noodle.sdk import NodeDef
from noodle_nodes.integrations_v2.specs import (
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
    return NodeManifest(
        id=spec.node_id,
        name=spec.name,
        category=spec.category,
        version=spec.version,
        description=spec.description,
        icon=spec.icon,
        role=spec.role,
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
            from noodle_nodes.integrations_v2.registry import execute_registered_operation

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
    func.__noodle_source__ = source
    func.__noodle_operation_spec__ = spec
    func.__noodle_operation_executor__ = executor
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
    func.__noodle_source__ = source
    func.__noodle_provider_trigger_spec__ = spec
    return NodeDef(
        func=func,
        manifest=trigger_manifest(spec),
        is_async=False,
        param_names=frozenset(param.name for param in spec.params),
        accepts_var_keyword=False,
        declared_id=spec.node_id,
    )
