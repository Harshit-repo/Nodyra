"""WooCommerce v2 operation specs and executors."""

from __future__ import annotations

import base64
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
            type="woocommerce",
            key="*",
            label="WooCommerce store",
            fields=["store_url", "consumer_key", "consumer_secret"],
            multi=True,
            test_service="woocommerce",
        ),
        description="WooCommerce store credentials (store URL, consumer key, consumer secret).",
    )


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    store_url = str(creds.get("store_url") or "").strip().rstrip("/")
    consumer_key = str(creds.get("consumer_key") or "")
    consumer_secret = str(creds.get("consumer_secret") or "")
    if not store_url:
        raise ValueError("woocommerce: store_url is required")
    if not consumer_key:
        raise ValueError("woocommerce: consumer_key is required")
    if not consumer_secret:
        raise ValueError("woocommerce: consumer_secret is required")
    for prefix in ("http://", "https://"):
        if store_url.startswith(prefix):
            store_url = store_url[len(prefix):]
    raw = f"{consumer_key}:{consumer_secret}"
    encoded = base64.b64encode(raw.encode()).decode()
    return ProviderTransport(
        provider="woocommerce",
        base_url=f"https://{store_url}/wp-json/wc/v3",
        default_headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {encoded}",
        },
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


WOOCOMMERCE_LIST_PRODUCTS_SPEC = OperationSpec(
    node_id="woocommerce_list_products_v2",
    name="WooCommerce List Products",
    provider="woocommerce",
    resource="product",
    operation="list",
    description="List products from WooCommerce.",
    icon="brand:woocommerce",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="limit",
            type="number",
            default=20,
            group="Options",
        ),
    ),
)

WOOCOMMERCE_GET_PRODUCT_SPEC = OperationSpec(
    node_id="woocommerce_get_product_v2",
    name="WooCommerce Get Product",
    provider="woocommerce",
    resource="product",
    operation="get",
    description="Get a single product from WooCommerce.",
    icon="brand:woocommerce",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="product_id", required=True),
    ),
)

WOOCOMMERCE_CREATE_PRODUCT_SPEC = OperationSpec(
    node_id="woocommerce_create_product_v2",
    name="WooCommerce Create Product",
    provider="woocommerce",
    resource="product",
    operation="create",
    description="Create a new product in WooCommerce.",
    icon="brand:woocommerce",
    params=(
        _credentials_param(),
        OperationParamSpec(name="name", required=True),
        OperationParamSpec(
            name="type",
            choices=("simple", "variable", "grouped", "external"),
        ),
        OperationParamSpec(name="regular_price", required=True),
        OperationParamSpec(name="description", multiline=True, group="Options"),
        OperationParamSpec(name="sku", group="Options"),
        OperationParamSpec(
            name="stock_quantity",
            type="number",
            group="Options",
        ),
        OperationParamSpec(
            name="stock_status",
            group="Options",
            choices=("instock", "outofstock", "onbackorder"),
        ),
    ),
)

WOOCOMMERCE_LIST_ORDERS_SPEC = OperationSpec(
    node_id="woocommerce_list_orders_v2",
    name="WooCommerce List Orders",
    provider="woocommerce",
    resource="order",
    operation="list",
    description="List orders from WooCommerce.",
    icon="brand:woocommerce",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="status",
            group="Options",
            choices=("pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"),
        ),
        OperationParamSpec(
            name="limit",
            type="number",
            default=20,
            group="Options",
        ),
    ),
)

WOOCOMMERCE_GET_ORDER_SPEC = OperationSpec(
    node_id="woocommerce_get_order_v2",
    name="WooCommerce Get Order",
    provider="woocommerce",
    resource="order",
    operation="get",
    description="Get a single order from WooCommerce.",
    icon="brand:woocommerce",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="order_id", required=True),
    ),
)

WOOCOMMERCE_LIST_CUSTOMERS_SPEC = OperationSpec(
    node_id="woocommerce_list_customers_v2",
    name="WooCommerce List Customers",
    provider="woocommerce",
    resource="customer",
    operation="list",
    description="List customers from WooCommerce.",
    icon="brand:woocommerce",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="limit",
            type="number",
            default=20,
            group="Options",
        ),
    ),
)


def list_products(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    limit: int = 20,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/products",
        operation="list_products",
        params={"per_page": str(max(1, int(limit or 20)))},
    )


def get_product(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    product_id: str = "",
) -> Any:
    if not product_id:
        raise ValueError("woocommerce_get_product_v2: product_id is required")
    return _transport(credentials).request(
        "GET",
        f"/products/{product_id}",
        operation="get_product",
    )


def create_product(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    name: str = "",
    type: str = "simple",
    regular_price: str = "",
    description: str = "",
    sku: str = "",
    stock_quantity: int | None = None,
    stock_status: str = "",
) -> Any:
    if not name:
        raise ValueError("woocommerce_create_product_v2: name is required")
    if not regular_price:
        raise ValueError("woocommerce_create_product_v2: regular_price is required")
    payload: dict[str, Any] = {
        "name": name,
        "type": type or "simple",
        "regular_price": regular_price,
    }
    if description:
        payload["description"] = description
    if sku:
        payload["sku"] = sku
    if stock_quantity is not None:
        payload["stock_quantity"] = int(stock_quantity)
    if stock_status:
        payload["stock_status"] = stock_status
    return _transport(credentials).request(
        "POST",
        "/products",
        operation="create_product",
        json_body=payload,
    )


def list_orders(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    status: str = "",
    limit: int = 20,
) -> Any:
    params: dict[str, str] = {"per_page": str(max(1, int(limit or 20)))}
    if status:
        params["status"] = status
    return _transport(credentials).request(
        "GET",
        "/orders",
        operation="list_orders",
        params=params,
    )


def get_order(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    order_id: str = "",
) -> Any:
    if not order_id:
        raise ValueError("woocommerce_get_order_v2: order_id is required")
    return _transport(credentials).request(
        "GET",
        f"/orders/{order_id}",
        operation="get_order",
    )


def list_customers(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    limit: int = 20,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/customers",
        operation="list_customers",
        params={"per_page": str(max(1, int(limit or 20)))},
    )


register_operation(WOOCOMMERCE_LIST_PRODUCTS_SPEC, list_products, node_registry=None)
register_operation(WOOCOMMERCE_GET_PRODUCT_SPEC, get_product, node_registry=None)
register_operation(WOOCOMMERCE_CREATE_PRODUCT_SPEC, create_product, node_registry=None)
register_operation(WOOCOMMERCE_LIST_ORDERS_SPEC, list_orders, node_registry=None)
register_operation(WOOCOMMERCE_GET_ORDER_SPEC, get_order, node_registry=None)
register_operation(WOOCOMMERCE_LIST_CUSTOMERS_SPEC, list_customers, node_registry=None)


WOOCOMMERCE_INTEGRATION = IntegrationSpec(
    id="woocommerce",
    name="WooCommerce",
    description="Manage WooCommerce products, orders, and customers.",
    icon="brand:woocommerce",
    credential_types=("woocommerce",),
    resources=(
        ResourceSpec(
            id="product",
            name="Product",
            operations=(
                WOOCOMMERCE_LIST_PRODUCTS_SPEC,
                WOOCOMMERCE_GET_PRODUCT_SPEC,
                WOOCOMMERCE_CREATE_PRODUCT_SPEC,
            ),
        ),
        ResourceSpec(
            id="order",
            name="Order",
            operations=(
                WOOCOMMERCE_LIST_ORDERS_SPEC,
                WOOCOMMERCE_GET_ORDER_SPEC,
            ),
        ),
        ResourceSpec(
            id="customer",
            name="Customer",
            operations=(WOOCOMMERCE_LIST_CUSTOMERS_SPEC,),
        ),
    ),
)

register_integration(WOOCOMMERCE_INTEGRATION)
