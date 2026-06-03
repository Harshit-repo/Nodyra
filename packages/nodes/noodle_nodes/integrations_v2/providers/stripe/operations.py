"""Stripe v2 operation specs and executors."""

from __future__ import annotations

from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_operation
from noodle_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec
from noodle_nodes.integrations_v2.transport import ProviderTransport

STRIPE_API_BASE = "https://api.stripe.com/v1"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="stripe",
            key="*",
            label="Stripe API key",
            fields=["api_key"],
            multi=True,
            test_service="stripe",
        ),
        description="Stripe API key.",
    )


STRIPE_CREATE_CUSTOMER_SPEC = OperationSpec(
    node_id="stripe_create_customer_v2",
    name="Stripe Create Customer V2",
    provider="stripe",
    resource="customer",
    operation="create",
    description="Create a Stripe customer using the v2 provider transport.",
    icon="card",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="email",
            placeholder="customer@example.com",
        ),
        OperationParamSpec(
            name="name",
            group="Options",
            placeholder="Customer name",
        ),
        OperationParamSpec(
            name="description",
            group="Options",
            placeholder="Optional description",
        ),
        OperationParamSpec(
            name="metadata",
            type="object",
            group="Options",
            description="Optional Stripe metadata object.",
        ),
    ),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items()}
    if isinstance(value, str):
        return {"api_key": value}
    return {}


def _api_key(credentials: Any) -> str:
    creds = _credentials_dict(credentials)
    return str(creds.get("api_key") or creds.get("token") or "")


def _transport(credentials: Any) -> ProviderTransport:
    api_key = _api_key(credentials)
    if not api_key:
        raise ValueError("stripe_create_customer_v2: credentials are required")
    return ProviderTransport(
        provider="stripe",
        base_url=STRIPE_API_BASE,
        default_headers={"Authorization": f"Bearer {api_key}"},
    )


def _dict_from_input(input_value: Any, value: dict[str, Any] | None = None) -> dict[str, Any]:
    if value is not None:
        return value
    return input_value if isinstance(input_value, dict) else {}


def _flatten_data(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in data.items():
        form_key = f"{prefix}[{key}]" if prefix else key
        if isinstance(value, dict):
            flattened.update(_flatten_data(value, form_key))
        elif value is not None:
            flattened[form_key] = value
    return flattened


def create_customer(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    email: str = "",
    name: str = "",
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> Any:
    source = _dict_from_input(input)
    data = {
        "email": email or source.get("email"),
        "name": name or source.get("name"),
        "description": description,
        "metadata": metadata or {},
    }
    return _transport(credentials).request(
        "POST",
        "/customers",
        operation="create_customer",
        data=_flatten_data(data),
    )


register_operation(STRIPE_CREATE_CUSTOMER_SPEC, create_customer)
