"""Declarative specs for generated v2 integration nodes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from noodle.models import CredentialSpec, NodeRole, ParamSpec, PortDataKind


@dataclass(frozen=True)
class OperationParamSpec:
    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""
    placeholder: str = ""
    choices: Sequence[Any] | None = None
    multiline: bool = False
    key_value: bool = False
    credential: CredentialSpec | None = None
    group: str | None = None
    display_name: str = ""
    widget: str = ""
    load_options: str | None = None
    credential_type: str | None = None
    required_scopes: Sequence[str] = field(default_factory=tuple)
    advanced: bool = False
    documentation_url: str = ""
    validation: dict[str, Any] | None = None

    def to_param_spec(self) -> ParamSpec:
        return ParamSpec(
            name=self.name,
            type=self.type,
            required=self.required,
            default=self.default,
            description=self.description,
            placeholder=self.placeholder,
            choices=list(self.choices) if self.choices is not None else None,
            multiline=self.multiline,
            key_value=self.key_value,
            credential=self.credential,
            group=self.group,
            display_name=self.display_name,
            widget=self.widget,
            load_options=self.load_options,
            credential_type=self.credential_type,
            required_scopes=[str(scope) for scope in self.required_scopes],
            advanced=self.advanced,
            documentation_url=self.documentation_url,
            validation=self.validation,
        )


@dataclass(frozen=True)
class OperationSpec:
    node_id: str
    name: str
    provider: str
    resource: str
    operation: str
    category: str = "Integrations"
    version: str = "1.0.0"
    description: str = ""
    icon: str | None = None
    role: NodeRole = NodeRole.executable
    params: Sequence[OperationParamSpec] = field(default_factory=tuple)
    input_kind: PortDataKind = PortDataKind.main
    output_kind: PortDataKind = PortDataKind.main

    @property
    def operation_key(self) -> str:
        return f"{self.provider}.{self.resource}.{self.operation}"


@dataclass(frozen=True)
class ResourceSpec:
    id: str
    name: str
    operations: Sequence[OperationSpec] = field(default_factory=tuple)


@dataclass(frozen=True)
class IntegrationSpec:
    id: str
    name: str
    credential_types: Sequence[str] = field(default_factory=tuple)
    resources: Sequence[ResourceSpec] = field(default_factory=tuple)


@dataclass(frozen=True)
class ProviderTriggerSubscription:
    """Provider-side subscription created for an active workflow trigger."""

    external_id: str
    config: dict[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ProviderTriggerActivationContext:
    """Input passed to a provider trigger's activation hook."""

    workflow_id: str
    workflow_version_id: str
    node_id: str
    callback_url: str
    params: dict[str, Any]


@dataclass(frozen=True)
class ProviderTriggerDeactivationContext:
    """Input passed to a provider trigger's deactivation hook."""

    workflow_id: str
    workflow_version_id: str | None
    node_id: str
    external_id: str
    params: dict[str, Any]
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderTriggerRequest:
    """Normalized inbound webhook delivery for a provider trigger."""

    headers: dict[str, str]
    query: dict[str, str]
    body: Any
    raw_body: bytes


@dataclass(frozen=True)
class ProviderTriggerEvent:
    """Normalized provider event after signature/challenge handling."""

    payload: dict[str, Any] | None
    dedupe_key: str | None = None
    response_body: Any = None
    response_status: int = 202
    response_headers: dict[str, str] = field(default_factory=dict)


ProviderTriggerActivate = Callable[
    [ProviderTriggerActivationContext], ProviderTriggerSubscription
]
ProviderTriggerDeactivate = Callable[[ProviderTriggerDeactivationContext], None]
ProviderTriggerHandleEvent = Callable[
    [ProviderTriggerRequest, dict[str, Any]], ProviderTriggerEvent
]


@dataclass(frozen=True)
class ProviderTriggerSpec:
    node_id: str
    name: str
    provider: str
    resource: str
    event: str
    category: str = "Triggers"
    version: str = "1.0.0"
    description: str = ""
    icon: str | None = None
    params: Sequence[OperationParamSpec] = field(default_factory=tuple)
    output_kind: PortDataKind = PortDataKind.main
    activate: ProviderTriggerActivate | None = None
    deactivate: ProviderTriggerDeactivate | None = None
    handle_event: ProviderTriggerHandleEvent | None = None

    @property
    def trigger_key(self) -> str:
        return f"{self.provider}.{self.resource}.{self.event}"
