"""Stripe v2 operation specs and executors."""

from __future__ import annotations

from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_operation
from nodyra_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec
from nodyra_nodes.integrations_v2.transport import ProviderTransport

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
    name="Stripe Create Customer",
    provider="stripe",
    resource="customer",
    operation="create",
    description="Create a Stripe customer using the v2 provider transport.",
    icon="brand:stripe",
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

STRIPE_GET_CUSTOMER_SPEC = OperationSpec(
    node_id="stripe_get_customer_v2",
    name="Stripe Get Customer",
    provider="stripe",
    resource="customer",
    operation="get",
    description="Retrieve a Stripe customer by ID.",
    icon="brand:stripe",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="customer_id", required=True, placeholder="cus_..."),
    ),
)

STRIPE_LIST_CUSTOMERS_SPEC = OperationSpec(
    node_id="stripe_list_customers_v2",
    name="Stripe List Customers",
    provider="stripe",
    resource="customer",
    operation="list",
    description="List Stripe customers, optionally filtered by email.",
    icon="brand:stripe",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="email",
            group="Filters",
            placeholder="customer@example.com",
        ),
        OperationParamSpec(
            name="limit",
            type="number",
            default=10,
            group="Options",
            description="Maximum number of customers to return (1-100).",
        ),
    ),
)

STRIPE_UPDATE_CUSTOMER_SPEC = OperationSpec(
    node_id="stripe_update_customer_v2",
    name="Stripe Update Customer",
    provider="stripe",
    resource="customer",
    operation="update",
    description="Update customer fields and metadata in Stripe.",
    icon="brand:stripe",
    params=(
        _credentials_param(),
        OperationParamSpec(name="customer_id", required=True, placeholder="cus_..."),
        OperationParamSpec(name="email", group="Options", placeholder="customer@example.com"),
        OperationParamSpec(name="name", group="Options", placeholder="Customer name"),
        OperationParamSpec(name="description", group="Options"),
        OperationParamSpec(
            name="metadata",
            type="object",
            group="Options",
            description="Metadata keys to set on the customer.",
        ),
    ),
)

STRIPE_CREATE_CHECKOUT_SESSION_SPEC = OperationSpec(
    node_id="stripe_create_checkout_session_v2",
    name="Stripe Create Checkout Session",
    provider="stripe",
    resource="checkout_session",
    operation="create",
    description="Create a hosted Stripe Checkout Session.",
    icon="brand:stripe",
    params=(
        _credentials_param(),
        OperationParamSpec(name="success_url", required=True, placeholder="https://.../success"),
        OperationParamSpec(name="cancel_url", required=True, placeholder="https://.../cancel"),
        OperationParamSpec(
            name="mode",
            default="payment",
            choices=("payment", "subscription", "setup"),
            group="Options",
        ),
        OperationParamSpec(
            name="line_items",
            type="array",
            description="Stripe line item objects. Blank can use price_id and quantity.",
        ),
        OperationParamSpec(name="price_id", group="Simple Line Item", placeholder="price_..."),
        OperationParamSpec(
            name="quantity",
            type="number",
            default=1,
            group="Simple Line Item",
        ),
        OperationParamSpec(name="customer_id", group="Options", placeholder="cus_..."),
        OperationParamSpec(
            name="customer_email",
            group="Options",
            placeholder="customer@example.com",
        ),
        OperationParamSpec(
            name="client_reference_id",
            group="Options",
            description="Optional ID to reconcile the session with your system.",
        ),
        OperationParamSpec(
            name="allow_promotion_codes",
            type="boolean",
            default=False,
            group="Options",
        ),
        OperationParamSpec(
            name="payment_method_types",
            type="array",
            group="Options",
            description="Optional payment method type list, for example ['card'].",
        ),
        OperationParamSpec(name="metadata", type="object", group="Options"),
    ),
)

STRIPE_CREATE_PAYMENT_INTENT_SPEC = OperationSpec(
    node_id="stripe_create_payment_intent_v2",
    name="Stripe Create Payment Intent",
    provider="stripe",
    resource="payment_intent",
    operation="create",
    description="Create a Stripe PaymentIntent.",
    icon="brand:stripe",
    params=(
        _credentials_param(),
        OperationParamSpec(name="amount", type="number", required=True, placeholder="2000"),
        OperationParamSpec(name="currency", default="usd", placeholder="usd"),
        OperationParamSpec(name="customer_id", group="Options", placeholder="cus_..."),
        OperationParamSpec(name="description", group="Options"),
        OperationParamSpec(
            name="receipt_email",
            group="Options",
            placeholder="customer@example.com",
        ),
        OperationParamSpec(
            name="payment_method",
            group="Options",
            placeholder="pm_...",
        ),
        OperationParamSpec(
            name="confirm",
            type="boolean",
            default=False,
            group="Options",
        ),
        OperationParamSpec(
            name="automatic_payment_methods",
            type="boolean",
            default=True,
            group="Options",
            description="Enable Stripe automatic payment methods.",
        ),
        OperationParamSpec(name="metadata", type="object", group="Options"),
    ),
)

STRIPE_GET_PAYMENT_INTENT_SPEC = OperationSpec(
    node_id="stripe_get_payment_intent_v2",
    name="Stripe Get Payment Intent",
    provider="stripe",
    resource="payment_intent",
    operation="get",
    description="Retrieve a Stripe PaymentIntent by ID.",
    icon="brand:stripe",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="payment_intent_id", required=True, placeholder="pi_..."),
    ),
)

STRIPE_LIST_PAYMENT_INTENTS_SPEC = OperationSpec(
    node_id="stripe_list_payment_intents_v2",
    name="Stripe List Payment Intents",
    provider="stripe",
    resource="payment_intent",
    operation="list",
    description="List Stripe PaymentIntents.",
    icon="brand:stripe",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="customer_id", group="Filters", placeholder="cus_..."),
        OperationParamSpec(name="limit", type="number", default=10, group="Options"),
    ),
)

STRIPE_CREATE_REFUND_SPEC = OperationSpec(
    node_id="stripe_create_refund_v2",
    name="Stripe Create Refund",
    provider="stripe",
    resource="refund",
    operation="create",
    description="Create a refund for a charge or PaymentIntent.",
    icon="brand:stripe",
    params=(
        _credentials_param(),
        OperationParamSpec(name="payment_intent", group="Payment", placeholder="pi_..."),
        OperationParamSpec(name="charge", group="Payment", placeholder="ch_..."),
        OperationParamSpec(
            name="amount",
            type="number",
            group="Options",
            description="Amount to refund in the smallest currency unit. Blank refunds all.",
        ),
        OperationParamSpec(
            name="reason",
            group="Options",
            choices=("duplicate", "fraudulent", "requested_by_customer"),
        ),
        OperationParamSpec(name="metadata", type="object", group="Options"),
    ),
)

STRIPE_LIST_PRODUCTS_SPEC = OperationSpec(
    node_id="stripe_list_products_v2",
    name="Stripe List Products",
    provider="stripe",
    resource="product",
    operation="list",
    description="List Stripe products.",
    icon="brand:stripe",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="active", type="boolean", default=None, group="Filters"),
        OperationParamSpec(name="limit", type="number", default=10, group="Options"),
    ),
)

STRIPE_LIST_PRICES_SPEC = OperationSpec(
    node_id="stripe_list_prices_v2",
    name="Stripe List Prices",
    provider="stripe",
    resource="price",
    operation="list",
    description="List Stripe prices, optionally scoped to a product.",
    icon="brand:stripe",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="product_id", group="Filters", placeholder="prod_..."),
        OperationParamSpec(name="active", type="boolean", default=None, group="Filters"),
        OperationParamSpec(name="limit", type="number", default=10, group="Options"),
    ),
)

STRIPE_CREATE_INVOICE_SPEC = OperationSpec(
    node_id="stripe_create_invoice_v2",
    name="Stripe Create Invoice",
    provider="stripe",
    resource="invoice",
    operation="create",
    description="Create a Stripe invoice draft for a customer.",
    icon="brand:stripe",
    params=(
        _credentials_param(),
        OperationParamSpec(name="customer_id", required=True, placeholder="cus_..."),
        OperationParamSpec(
            name="collection_method",
            default="charge_automatically",
            choices=("charge_automatically", "send_invoice"),
            group="Options",
        ),
        OperationParamSpec(name="days_until_due", type="number", default=None, group="Options"),
        OperationParamSpec(name="auto_advance", type="boolean", default=None, group="Options"),
        OperationParamSpec(name="metadata", type="object", group="Options"),
    ),
)

STRIPE_LIST_INVOICES_SPEC = OperationSpec(
    node_id="stripe_list_invoices_v2",
    name="Stripe List Invoices",
    provider="stripe",
    resource="invoice",
    operation="list",
    description="List Stripe invoices.",
    icon="brand:stripe",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="customer_id", group="Filters", placeholder="cus_..."),
        OperationParamSpec(
            name="status",
            group="Filters",
            choices=("draft", "open", "paid", "uncollectible", "void"),
        ),
        OperationParamSpec(name="limit", type="number", default=10, group="Options"),
    ),
)

STRIPE_CREATE_SUBSCRIPTION_SPEC = OperationSpec(
    node_id="stripe_create_subscription_v2",
    name="Stripe Create Subscription",
    provider="stripe",
    resource="subscription",
    operation="create",
    description="Create a Stripe subscription for a customer.",
    icon="brand:stripe",
    params=(
        _credentials_param(),
        OperationParamSpec(name="customer_id", required=True, placeholder="cus_..."),
        OperationParamSpec(
            name="items",
            type="array",
            description="Subscription item objects. Blank can use price_id and quantity.",
        ),
        OperationParamSpec(name="price_id", group="Simple Item", placeholder="price_..."),
        OperationParamSpec(name="quantity", type="number", default=1, group="Simple Item"),
        OperationParamSpec(
            name="payment_behavior",
            default="default_incomplete",
            choices=(
                "allow_incomplete",
                "default_incomplete",
                "error_if_incomplete",
                "pending_if_incomplete",
            ),
            group="Options",
        ),
        OperationParamSpec(name="trial_period_days", type="number", default=None, group="Options"),
        OperationParamSpec(name="metadata", type="object", group="Options"),
    ),
)

STRIPE_LIST_SUBSCRIPTIONS_SPEC = OperationSpec(
    node_id="stripe_list_subscriptions_v2",
    name="Stripe List Subscriptions",
    provider="stripe",
    resource="subscription",
    operation="list",
    description="List Stripe subscriptions.",
    icon="brand:stripe",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="customer_id", group="Filters", placeholder="cus_..."),
        OperationParamSpec(name="price_id", group="Filters", placeholder="price_..."),
        OperationParamSpec(name="status", default="all", group="Filters"),
        OperationParamSpec(name="limit", type="number", default=10, group="Options"),
    ),
)

STRIPE_CANCEL_SUBSCRIPTION_SPEC = OperationSpec(
    node_id="stripe_cancel_subscription_v2",
    name="Stripe Cancel Subscription",
    provider="stripe",
    resource="subscription",
    operation="cancel",
    description="Cancel a Stripe subscription.",
    icon="brand:stripe",
    params=(
        _credentials_param(),
        OperationParamSpec(name="subscription_id", required=True, placeholder="sub_..."),
        OperationParamSpec(name="invoice_now", type="boolean", default=None, group="Options"),
        OperationParamSpec(name="prorate", type="boolean", default=None, group="Options"),
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
        raise ValueError("stripe v2: credentials are required")
    return ProviderTransport(
        provider="stripe",
        base_url=STRIPE_API_BASE,
        default_headers={"Authorization": f"Bearer {api_key}"},
    )


def _dict_from_input(input_value: Any, value: dict[str, Any] | None = None) -> dict[str, Any]:
    if value is not None:
        return value
    return input_value if isinstance(input_value, dict) else {}


def _stripe_scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _flatten_data(data: Any, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if isinstance(data, list):
        for index, value in enumerate(data):
            form_key = f"{prefix}[{index}]"
            if isinstance(value, (dict, list)):
                flattened.update(_flatten_data(value, form_key))
            elif value is not None:
                flattened[form_key] = _stripe_scalar(value)
        return flattened
    if not isinstance(data, dict):
        return flattened
    for key, value in data.items():
        form_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten_data(value, form_key))
        elif isinstance(value, list):
            flattened.update(_flatten_data(value, form_key))
        elif value is not None:
            flattened[form_key] = _stripe_scalar(value)
    return flattened


def _limit(value: Any, default: int = 10) -> int:
    return max(1, min(100, int(value or default)))


def _require_id(node_id: str, value: str, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{node_id}: {label} is required")
    return clean


def _data_without_blanks(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in data.items()
        if value is not None and value != "" and value != {}
    }


def _line_items(
    *,
    input_value: Any,
    line_items: list[dict[str, Any]] | None,
    price_id: str,
    quantity: int,
) -> list[dict[str, Any]]:
    if line_items:
        return line_items
    if isinstance(input_value, dict) and isinstance(input_value.get("line_items"), list):
        return input_value["line_items"]
    if price_id:
        return [{"price": price_id, "quantity": max(1, int(quantity or 1))}]
    return []


def _subscription_items(
    *,
    input_value: Any,
    items: list[dict[str, Any]] | None,
    price_id: str,
    quantity: int,
) -> list[dict[str, Any]]:
    if items:
        return items
    if isinstance(input_value, dict) and isinstance(input_value.get("items"), list):
        return input_value["items"]
    if price_id:
        return [{"price": price_id, "quantity": max(1, int(quantity or 1))}]
    return []


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


def get_customer(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    customer_id: str = "",
) -> Any:
    customer = _require_id("stripe_get_customer_v2", customer_id, "customer_id")
    return _transport(credentials).request(
        "GET",
        f"/customers/{customer}",
        operation="get_customer",
    )


def list_customers(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    email: str = "",
    limit: int = 10,
) -> Any:
    params: dict[str, Any] = {"limit": _limit(limit)}
    if email:
        params["email"] = email
    return _transport(credentials).request(
        "GET",
        "/customers",
        operation="list_customers",
        params=params,
    )


def update_customer(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    customer_id: str = "",
    email: str = "",
    name: str = "",
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> Any:
    source = _dict_from_input(input)
    customer = _require_id("stripe_update_customer_v2", customer_id, "customer_id")
    data = _data_without_blanks(
        {
            "email": email or source.get("email"),
            "name": name or source.get("name"),
            "description": description or source.get("description"),
            "metadata": metadata if metadata is not None else source.get("metadata", {}),
        }
    )
    return _transport(credentials).request(
        "POST",
        f"/customers/{customer}",
        operation="update_customer",
        data=_flatten_data(data),
    )


def create_checkout_session(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    success_url: str = "",
    cancel_url: str = "",
    mode: str = "payment",
    line_items: list[dict[str, Any]] | None = None,
    price_id: str = "",
    quantity: int = 1,
    customer_id: str = "",
    customer_email: str = "",
    client_reference_id: str = "",
    allow_promotion_codes: bool = False,
    payment_method_types: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    source = _dict_from_input(input)
    session_line_items = _line_items(
        input_value=input,
        line_items=line_items,
        price_id=price_id or str(source.get("price_id") or ""),
        quantity=quantity,
    )
    if not success_url:
        raise ValueError("stripe_create_checkout_session_v2: success_url is required")
    if not cancel_url:
        raise ValueError("stripe_create_checkout_session_v2: cancel_url is required")
    if mode != "setup" and not session_line_items:
        raise ValueError("stripe_create_checkout_session_v2: line_items or price_id is required")
    data = _data_without_blanks(
        {
            "success_url": success_url,
            "cancel_url": cancel_url,
            "mode": mode,
            "line_items": session_line_items,
            "customer": customer_id or source.get("customer_id"),
            "customer_email": customer_email or source.get("customer_email"),
            "client_reference_id": client_reference_id or source.get("client_reference_id"),
            "allow_promotion_codes": bool(allow_promotion_codes),
            "payment_method_types": payment_method_types or source.get("payment_method_types"),
            "metadata": metadata if metadata is not None else source.get("metadata", {}),
        }
    )
    return _transport(credentials).request(
        "POST",
        "/checkout/sessions",
        operation="create_checkout_session",
        data=_flatten_data(data),
    )


def create_payment_intent(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    amount: int = 0,
    currency: str = "usd",
    customer_id: str = "",
    description: str = "",
    receipt_email: str = "",
    payment_method: str = "",
    confirm: bool = False,
    automatic_payment_methods: bool = True,
    metadata: dict[str, Any] | None = None,
) -> Any:
    source = _dict_from_input(input)
    amount_value = int(amount or source.get("amount") or 0)
    if amount_value <= 0:
        raise ValueError("stripe_create_payment_intent_v2: amount is required")
    data = _data_without_blanks(
        {
            "amount": amount_value,
            "currency": currency or source.get("currency") or "usd",
            "customer": customer_id or source.get("customer_id"),
            "description": description or source.get("description"),
            "receipt_email": receipt_email or source.get("receipt_email"),
            "payment_method": payment_method or source.get("payment_method"),
            "confirm": bool(confirm),
            "automatic_payment_methods": {"enabled": bool(automatic_payment_methods)},
            "metadata": metadata if metadata is not None else source.get("metadata", {}),
        }
    )
    return _transport(credentials).request(
        "POST",
        "/payment_intents",
        operation="create_payment_intent",
        data=_flatten_data(data),
    )


def get_payment_intent(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    payment_intent_id: str = "",
) -> Any:
    intent_id = _require_id(
        "stripe_get_payment_intent_v2",
        payment_intent_id,
        "payment_intent_id",
    )
    return _transport(credentials).request(
        "GET",
        f"/payment_intents/{intent_id}",
        operation="get_payment_intent",
    )


def list_payment_intents(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    customer_id: str = "",
    limit: int = 10,
) -> Any:
    params: dict[str, Any] = {"limit": _limit(limit)}
    if customer_id:
        params["customer"] = customer_id
    return _transport(credentials).request(
        "GET",
        "/payment_intents",
        operation="list_payment_intents",
        params=params,
    )


def create_refund(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    payment_intent: str = "",
    charge: str = "",
    amount: int | None = None,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> Any:
    source = _dict_from_input(input)
    data = _data_without_blanks(
        {
            "payment_intent": payment_intent or source.get("payment_intent"),
            "charge": charge or source.get("charge"),
            "amount": amount if amount is not None else source.get("amount"),
            "reason": reason or source.get("reason"),
            "metadata": metadata if metadata is not None else source.get("metadata", {}),
        }
    )
    if not data.get("payment_intent") and not data.get("charge"):
        raise ValueError("stripe_create_refund_v2: payment_intent or charge is required")
    return _transport(credentials).request(
        "POST",
        "/refunds",
        operation="create_refund",
        data=_flatten_data(data),
    )


def create_invoice(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    customer_id: str = "",
    collection_method: str = "charge_automatically",
    days_until_due: int | None = None,
    auto_advance: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    source = _dict_from_input(input)
    customer = customer_id or str(source.get("customer_id") or "")
    if not customer:
        raise ValueError("stripe_create_invoice_v2: customer_id is required")
    data = _data_without_blanks(
        {
            "customer": customer,
            "collection_method": collection_method,
            "days_until_due": days_until_due,
            "auto_advance": auto_advance,
            "metadata": metadata if metadata is not None else source.get("metadata", {}),
        }
    )
    return _transport(credentials).request(
        "POST",
        "/invoices",
        operation="create_invoice",
        data=_flatten_data(data),
    )


def list_invoices(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    customer_id: str = "",
    status: str = "",
    limit: int = 10,
) -> Any:
    params: dict[str, Any] = {"limit": _limit(limit)}
    if customer_id:
        params["customer"] = customer_id
    if status:
        params["status"] = status
    return _transport(credentials).request(
        "GET",
        "/invoices",
        operation="list_invoices",
        params=params,
    )


def create_subscription(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    customer_id: str = "",
    items: list[dict[str, Any]] | None = None,
    price_id: str = "",
    quantity: int = 1,
    payment_behavior: str = "default_incomplete",
    trial_period_days: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    source = _dict_from_input(input)
    customer = customer_id or str(source.get("customer_id") or "")
    subscription_items = _subscription_items(
        input_value=input,
        items=items,
        price_id=price_id or str(source.get("price_id") or ""),
        quantity=quantity,
    )
    if not customer:
        raise ValueError("stripe_create_subscription_v2: customer_id is required")
    if not subscription_items:
        raise ValueError("stripe_create_subscription_v2: items or price_id is required")
    data = _data_without_blanks(
        {
            "customer": customer,
            "items": subscription_items,
            "payment_behavior": payment_behavior,
            "trial_period_days": trial_period_days,
            "metadata": metadata if metadata is not None else source.get("metadata", {}),
        }
    )
    return _transport(credentials).request(
        "POST",
        "/subscriptions",
        operation="create_subscription",
        data=_flatten_data(data),
    )


def list_subscriptions(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    customer_id: str = "",
    price_id: str = "",
    status: str = "all",
    limit: int = 10,
) -> Any:
    params: dict[str, Any] = {"limit": _limit(limit)}
    if customer_id:
        params["customer"] = customer_id
    if price_id:
        params["price"] = price_id
    if status:
        params["status"] = status
    return _transport(credentials).request(
        "GET",
        "/subscriptions",
        operation="list_subscriptions",
        params=params,
    )


def cancel_subscription(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    subscription_id: str = "",
    invoice_now: bool | None = None,
    prorate: bool | None = None,
) -> Any:
    subscription = _require_id(
        "stripe_cancel_subscription_v2",
        subscription_id,
        "subscription_id",
    )
    data = _data_without_blanks({"invoice_now": invoice_now, "prorate": prorate})
    return _transport(credentials).request(
        "DELETE",
        f"/subscriptions/{subscription}",
        operation="cancel_subscription",
        data=_flatten_data(data),
    )


def list_products(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    active: bool | None = None,
    limit: int = 10,
) -> Any:
    params: dict[str, Any] = {"limit": _limit(limit)}
    if active is not None:
        params["active"] = str(bool(active)).lower()
    return _transport(credentials).request(
        "GET",
        "/products",
        operation="list_products",
        params=params,
    )


def list_prices(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    product_id: str = "",
    active: bool | None = None,
    limit: int = 10,
) -> Any:
    params: dict[str, Any] = {"limit": _limit(limit)}
    if product_id:
        params["product"] = product_id
    if active is not None:
        params["active"] = str(bool(active)).lower()
    return _transport(credentials).request(
        "GET",
        "/prices",
        operation="list_prices",
        params=params,
    )


register_operation(STRIPE_CREATE_CUSTOMER_SPEC, create_customer)
register_operation(STRIPE_GET_CUSTOMER_SPEC, get_customer)
register_operation(STRIPE_LIST_CUSTOMERS_SPEC, list_customers)
register_operation(STRIPE_UPDATE_CUSTOMER_SPEC, update_customer)
register_operation(STRIPE_CREATE_CHECKOUT_SESSION_SPEC, create_checkout_session)
register_operation(STRIPE_CREATE_PAYMENT_INTENT_SPEC, create_payment_intent)
register_operation(STRIPE_GET_PAYMENT_INTENT_SPEC, get_payment_intent)
register_operation(STRIPE_LIST_PAYMENT_INTENTS_SPEC, list_payment_intents)
register_operation(STRIPE_CREATE_REFUND_SPEC, create_refund)
register_operation(STRIPE_LIST_PRODUCTS_SPEC, list_products)
register_operation(STRIPE_LIST_PRICES_SPEC, list_prices)
register_operation(STRIPE_CREATE_INVOICE_SPEC, create_invoice)
register_operation(STRIPE_LIST_INVOICES_SPEC, list_invoices)
register_operation(STRIPE_CREATE_SUBSCRIPTION_SPEC, create_subscription)
register_operation(STRIPE_LIST_SUBSCRIPTIONS_SPEC, list_subscriptions)
register_operation(STRIPE_CANCEL_SUBSCRIPTION_SPEC, cancel_subscription)
