"""Operation registry for spec-generated v2 integration nodes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from noodle.sdk import NodeDef, NodeRegistry
from noodle.sdk import registry as default_node_registry
from noodle_nodes.integrations_v2.node_factory import (
    make_integration_node_def,
    make_operation_node_def,
    make_provider_trigger_node_def,
)
from noodle_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationSpec,
    ProviderTriggerSpec,
)

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


@dataclass
class RegisteredIntegration:
    spec: IntegrationSpec
    node_def: NodeDef


_operations: dict[str, RegisteredOperation] = {}
# Secondary index keyed by ``provider.resource.operation`` so a consolidated
# integration node can resolve its executor from the selected resource+operation.
_operations_by_key: dict[str, RegisteredOperation] = {}
_provider_triggers: dict[str, RegisteredProviderTrigger] = {}
_integrations: dict[str, RegisteredIntegration] = {}


def clear_registered_operations() -> None:
    _operations.clear()
    _operations_by_key.clear()


def clear_registered_integrations() -> None:
    _integrations.clear()


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
    registered = RegisteredOperation(
        spec=spec,
        executor=executor,
        node_def=node_def,
    )
    _operations[spec.node_id] = registered
    _operations_by_key[spec.operation_key] = registered
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


def resolve_operation_node_id(provider: str, resource: str, operation: str) -> str:
    """Map a (provider, resource, operation) selection to its operation node id."""
    key = f"{provider}.{resource}.{operation}"
    try:
        return _operations_by_key[key].spec.node_id
    except KeyError as exc:
        raise KeyError(f"Unknown integration operation: {key}") from exc


def execute_integration_operation(
    provider: str,
    resource: str,
    operation: str,
    **kwargs: Any,
) -> Any:
    """Dispatch a consolidated integration node to the selected operation.

    The consolidated node passes the union of every operation's params as
    keyword args; here we resolve the executor for the chosen
    ``resource``/``operation`` and forward only the kwargs that operation
    actually declares (its executors take explicit args, not ``**kwargs``).
    """
    key = f"{provider}.{resource}.{operation}"
    try:
        registered = _operations_by_key[key]
    except KeyError as exc:
        raise KeyError(f"Unknown integration operation: {key}") from exc
    allowed = registered.node_def.param_names
    filtered = {name: value for name, value in kwargs.items() if name in allowed}
    return registered.executor(**filtered)


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


def register_integration(
    spec: IntegrationSpec,
    *,
    node_registry: NodeRegistry | None = default_node_registry,
) -> NodeDef:
    """Register the single consolidated palette node for an integration provider.

    The per-operation executors must already be registered (via
    ``register_operation``) so the generated node can dispatch to them.
    """
    if spec.id in _integrations:
        raise ValueError(f"Duplicate integration id: {spec.id}")
    node_def = make_integration_node_def(spec)
    _integrations[spec.id] = RegisteredIntegration(spec=spec, node_def=node_def)
    if node_registry is not None:
        node_registry.register(node_def)
    return node_def


def get_registered_integration(integration_id: str) -> RegisteredIntegration:
    try:
        return _integrations[integration_id]
    except KeyError as exc:
        raise KeyError(f"Unknown integration: {integration_id}") from exc


def registered_integration_specs() -> list[IntegrationSpec]:
    return [integration.spec for integration in _integrations.values()]
