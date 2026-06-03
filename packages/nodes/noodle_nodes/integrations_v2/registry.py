"""Operation registry for spec-generated v2 integration nodes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from noodle.sdk import NodeDef, NodeRegistry
from noodle.sdk import registry as default_node_registry
from noodle_nodes.integrations_v2.node_factory import (
    make_operation_node_def,
    make_provider_trigger_node_def,
)
from noodle_nodes.integrations_v2.specs import OperationSpec, ProviderTriggerSpec

OperationExecutor = Callable[..., Any]


@dataclass
class RegisteredOperation:
    spec: OperationSpec
    executor: OperationExecutor
    node_def: NodeDef


@dataclass
class RegisteredProviderTrigger:
    spec: ProviderTriggerSpec
    node_def: NodeDef


_operations: dict[str, RegisteredOperation] = {}
_provider_triggers: dict[str, RegisteredProviderTrigger] = {}


def clear_registered_operations() -> None:
    _operations.clear()


def clear_registered_provider_triggers() -> None:
    _provider_triggers.clear()


def unregister_operation(node_id: str) -> None:
    _operations.pop(node_id, None)


def unregister_provider_trigger(node_id: str) -> None:
    _provider_triggers.pop(node_id, None)


def register_operation(
    spec: OperationSpec,
    executor: OperationExecutor,
    *,
    node_registry: NodeRegistry | None = default_node_registry,
) -> NodeDef:
    if spec.node_id in _operations:
        raise ValueError(f"Duplicate operation node id: {spec.node_id}")
    node_def = make_operation_node_def(spec, executor)
    _operations[spec.node_id] = RegisteredOperation(
        spec=spec,
        executor=executor,
        node_def=node_def,
    )
    if node_registry is not None:
        node_registry.register(node_def)
    return node_def


def get_registered_operation(node_id: str) -> RegisteredOperation:
    try:
        return _operations[node_id]
    except KeyError as exc:
        raise KeyError(f"Unknown integration operation node: {node_id}") from exc


def execute_registered_operation(node_id: str, **kwargs: Any) -> Any:
    operation = get_registered_operation(node_id)
    return operation.executor(**kwargs)


def registered_operation_specs() -> list[OperationSpec]:
    return [operation.spec for operation in _operations.values()]


def register_provider_trigger(
    spec: ProviderTriggerSpec,
    *,
    node_registry: NodeRegistry | None = default_node_registry,
) -> NodeDef:
    if spec.node_id in _provider_triggers:
        raise ValueError(f"Duplicate provider trigger node id: {spec.node_id}")
    node_def = make_provider_trigger_node_def(spec)
    _provider_triggers[spec.node_id] = RegisteredProviderTrigger(
        spec=spec,
        node_def=node_def,
    )
    if node_registry is not None:
        node_registry.register(node_def)
    return node_def


def get_registered_provider_trigger(node_id: str) -> RegisteredProviderTrigger:
    try:
        return _provider_triggers[node_id]
    except KeyError as exc:
        raise KeyError(f"Unknown provider trigger node: {node_id}") from exc


def is_registered_provider_trigger(node_id: str) -> bool:
    return node_id in _provider_triggers


def registered_provider_trigger_specs() -> list[ProviderTriggerSpec]:
    return [trigger.spec for trigger in _provider_triggers.values()]
