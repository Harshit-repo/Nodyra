"""Shopify v2 operation specs and executors."""

from __future__ import annotations

import json
from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_integration, register_operation
from noodle_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="shopify",
            key="*",
            label="Shopify store",
            fields=["store_url", "access_token"],
            multi=True,
            test_service="shopify",
        ),
        description="Shopify store credentials (store URL and access token).",
    )


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    store_url = str(creds.get("store_url") or "").strip().rstrip("/")
    access_token = str(creds.get("access_token") or "")
    if not store_url:
        raise ValueError("shopify: store_url is required")
    if not access_token:
        raise ValueError("shopify: access_token is required")
    for prefix in ("http://", "https://"):
        if store_url.startswith(prefix):
            store_url = store_url[len(prefix):]
    return ProviderTransport(
        provider="shopify",
        base_url=f"https://{store_url}",
        default_headers={
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": access_token,
        },
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


SHOPIFY_LIST_ORDERS_SPEC = OperationSpec(
    node_id="shopify_list_orders_v2",
    name="Shopify List Orders",
    provider="shopify",
    resource="order",
    operation="list",
    description="List orders from Shopify.",
    icon="brand:shopify",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="status",
            default="any",
            choices=("any", "open", "closed", "cancelled"),
            group="Options",
        ),
        OperationParamSpec(
            name="limit",
            type="number",
            default=50,
            group="Options",
        ),
    ),
)

SHOPIFY_GET_ORDER_SPEC = OperationSpec(
    node_id="shopify_get_order_v2",
    name="Shopify Get Order",
    provider="shopify",
    resource="order",
    operation="get",
    description="Get a single order from Shopify.",
    icon="brand:shopify",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="order_id", required=True),
    ),
)

SHOPIFY_CREATE_ORDER_SPEC = OperationSpec(
    node_id="shopify_create_order_v2",
    name="Shopify Create Order",
    provider="shopify",
    resource="order",
    operation="create",
    description="Create a new order in Shopify.",
    icon="brand:shopify",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="line_items",
            type="json",
            multiline=True,
            placeholder='[{"title":"Product","quantity":1,"price":"10.00"}]',
        ),
        OperationParamSpec(name="email", group="Options"),
        OperationParamSpec(name="note", group="Options"),
        OperationParamSpec(
            name="financial_status",
            group="Options",
            choices=("pending", "authorized", "paid", "refunded"),
        ),
    ),
)

SHOPIFY_LIST_PRODUCTS_SPEC = OperationSpec(
    node_id="shopify_list_products_v2",
    name="Shopify List Products",
    provider="shopify",
    resource="product",
    operation="list",
    description="List products from Shopify.",
    icon="brand:shopify",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="limit",
            type="number",
            default=50,
            group="Options",
        ),
    ),
)

SHOPIFY_GET_PRODUCT_SPEC = OperationSpec(
    node_id="shopify_get_product_v2",
    name="Shopify Get Product",
    provider="shopify",
    resource="product",
    operation="get",
    description="Get a single product from Shopify.",
    icon="brand:shopify",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="product_id", required=True),
    ),
)

SHOPIFY_LIST_CUSTOMERS_SPEC = OperationSpec(
    node_id="shopify_list_customers_v2",
    name="Shopify List Customers",
    provider="shopify",
    resource="customer",
    operation="list",
    description="List customers from Shopify.",
    icon="brand:shopify",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="limit",
            type="number",
            default=50,
            group="Options",
        ),
    ),
)

SHOPIFY_GET_CUSTOMER_SPEC = OperationSpec(
    node_id="shopify_get_customer_v2",
    name="Shopify Get Customer",
    provider="shopify",
    resource="customer",
    operation="get",
    description="Get a single customer from Shopify.",
    icon="brand:shopify",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="customer_id", required=True),
    ),
)


def list_orders(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    status: str = "any",
    limit: int = 50,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/admin/api/2024-10/orders.json",
        operation="list_orders",
        params={"status": status, "limit": str(max(1, int(limit or 50)))},
    )


def get_order(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    order_id: str = "",
) -> Any:
    if not order_id:
        raise ValueError("shopify_get_order_v2: order_id is required")
    return _transport(credentials).request(
        "GET",
        f"/admin/api/2024-10/orders/{order_id}.json",
        operation="get_order",
    )


def create_order(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    line_items: Any = None,
    email: str = "",
    note: str = "",
    financial_status: str = "",
) -> Any:
    payload: dict[str, Any] = {"order": {}}
    if line_items is not None:
        if isinstance(line_items, str):
            parsed = json.loads(line_items) if line_items.strip() else None
        else:
            parsed = line_items
        if parsed:
            payload["order"]["line_items"] = parsed
    if email:
        payload["order"]["email"] = email
    if note:
        payload["order"]["note"] = note
    if financial_status:
        payload["order"]["financial_status"] = financial_status
    return _transport(credentials).request(
        "POST",
        "/admin/api/2024-10/orders.json",
        operation="create_order",
        json_body=payload,
    )


def list_products(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    limit: int = 50,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/admin/api/2024-10/products.json",
        operation="list_products",
        params={"limit": str(max(1, int(limit or 50)))},
    )


def get_product(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    product_id: str = "",
) -> Any:
    if not product_id:
        raise ValueError("shopify_get_product_v2: product_id is required")
    return _transport(credentials).request(
        "GET",
        f"/admin/api/2024-10/products/{product_id}.json",
        operation="get_product",
    )


def list_customers(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    limit: int = 50,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/admin/api/2024-10/customers.json",
        operation="list_customers",
        params={"limit": str(max(1, int(limit or 50)))},
    )


def get_customer(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    customer_id: str = "",
) -> Any:
    if not customer_id:
        raise ValueError("shopify_get_customer_v2: customer_id is required")
    return _transport(credentials).request(
        "GET",
        f"/admin/api/2024-10/customers/{customer_id}.json",
        operation="get_customer",
    )


register_operation(SHOPIFY_LIST_ORDERS_SPEC, list_orders, node_registry=None)
register_operation(SHOPIFY_GET_ORDER_SPEC, get_order, node_registry=None)
register_operation(SHOPIFY_CREATE_ORDER_SPEC, create_order, node_registry=None)
register_operation(SHOPIFY_LIST_PRODUCTS_SPEC, list_products, node_registry=None)
register_operation(SHOPIFY_GET_PRODUCT_SPEC, get_product, node_registry=None)
register_operation(SHOPIFY_LIST_CUSTOMERS_SPEC, list_customers, node_registry=None)
register_operation(SHOPIFY_GET_CUSTOMER_SPEC, get_customer, node_registry=None)


SHOPIFY_INTEGRATION = IntegrationSpec(
    id="shopify",
    name="Shopify",
    description="Manage Shopify orders, products, and customers.",
    icon="brand:shopify",
    credential_types=("shopify",),
    resources=(
        ResourceSpec(
            id="order",
            name="Order",
            operations=(
                SHOPIFY_LIST_ORDERS_SPEC,
                SHOPIFY_GET_ORDER_SPEC,
                SHOPIFY_CREATE_ORDER_SPEC,
            ),
        ),
        ResourceSpec(
            id="product",
            name="Product",
            operations=(
                SHOPIFY_LIST_PRODUCTS_SPEC,
                SHOPIFY_GET_PRODUCT_SPEC,
            ),
        ),
        ResourceSpec(
            id="customer",
            name="Customer",
            operations=(
                SHOPIFY_LIST_CUSTOMERS_SPEC,
                SHOPIFY_GET_CUSTOMER_SPEC,
            ),
        ),
    ),
)

register_integration(SHOPIFY_INTEGRATION)
