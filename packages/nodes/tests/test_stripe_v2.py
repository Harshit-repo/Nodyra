from typing import Any
from unittest.mock import MagicMock

import noodle_nodes  # noqa: F401 - importing registers provider nodes
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.stripe import operations


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def test_stripe_v2_node_is_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}
    expected = {
        "stripe_create_customer_v2": ("Stripe Create Customer", True),
        "stripe_get_customer_v2": ("Stripe Get Customer", False),
        "stripe_list_customers_v2": ("Stripe List Customers", False),
        "stripe_update_customer_v2": ("Stripe Update Customer", True),
        "stripe_create_checkout_session_v2": ("Stripe Create Checkout Session", True),
        "stripe_create_payment_intent_v2": ("Stripe Create Payment Intent", True),
        "stripe_get_payment_intent_v2": ("Stripe Get Payment Intent", False),
        "stripe_list_payment_intents_v2": ("Stripe List Payment Intents", False),
        "stripe_create_refund_v2": ("Stripe Create Refund", True),
        "stripe_list_products_v2": ("Stripe List Products", False),
        "stripe_list_prices_v2": ("Stripe List Prices", False),
        "stripe_create_invoice_v2": ("Stripe Create Invoice", True),
        "stripe_list_invoices_v2": ("Stripe List Invoices", False),
        "stripe_create_subscription_v2": ("Stripe Create Subscription", True),
        "stripe_list_subscriptions_v2": ("Stripe List Subscriptions", False),
        "stripe_cancel_subscription_v2": ("Stripe Cancel Subscription", True),
    }

    for node_id, (name, side_effecting) in expected.items():
        manifest = manifests[node_id]
        assert manifest.name == name
        assert manifest.icon == "brand:stripe"
        assert manifest.category == "Integrations"
        assert manifest.usable_as_tool is True
        assert manifest.tool_side_effecting is side_effecting

    params = {param.name: param for param in manifests["stripe_create_customer_v2"].params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "stripe"
    assert params["credentials"].credential.multi is True
    assert params["credentials"].credential.test_service == "stripe"
    assert params["metadata"].group == "Options"


def test_stripe_v2_generated_source_is_available() -> None:
    source = getattr(
        registry.get("stripe_create_customer_v2").func,
        "__noodle_source__",
        "",
    )

    assert "def stripe_create_customer_v2(" in source
    assert "credentials=None" in source
    assert "execute_registered_operation" in source
    assert "stripe.customer.create" in source


def test_stripe_create_customer_v2_flattens_metadata(monkeypatch) -> None:
    transport = _mock_transport({"id": "cus_123"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = registry.get("stripe_create_customer_v2").func(
        input={"email": "ada@example.com", "name": "Ada"},
        credentials={"api_key": "sk_test"},
        metadata={"plan": "pro"},
    )

    assert result == {"id": "cus_123"}
    transport.request.assert_called_once_with(
        "POST",
        "/customers",
        operation="create_customer",
        data={
            "email": "ada@example.com",
            "name": "Ada",
            "metadata[plan]": "pro",
        },
    )


def test_stripe_read_nodes_use_query_params(monkeypatch) -> None:
    transport = _mock_transport({"data": []})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("stripe_list_customers_v2").func(
        input=None,
        credentials={"api_key": "sk_test"},
        email="ada@example.com",
        limit=250,
    )
    registry.get("stripe_get_customer_v2").func(
        input=None,
        credentials={"api_key": "sk_test"},
        customer_id="cus_123",
    )

    assert transport.request.call_args_list[0].args == ("GET", "/customers")
    assert transport.request.call_args_list[0].kwargs == {
        "operation": "list_customers",
        "params": {"limit": 100, "email": "ada@example.com"},
    }
    assert transport.request.call_args_list[1].args == ("GET", "/customers/cus_123")
    assert transport.request.call_args_list[1].kwargs == {
        "operation": "get_customer",
    }


def test_stripe_update_customer_v2_uses_input_and_metadata(monkeypatch) -> None:
    transport = _mock_transport({"id": "cus_123"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("stripe_update_customer_v2").func(
        input={"name": "Ada Lovelace", "metadata": {"tier": "gold"}},
        credentials={"api_key": "sk_test"},
        customer_id="cus_123",
        email="ada@example.com",
    )

    transport.request.assert_called_once_with(
        "POST",
        "/customers/cus_123",
        operation="update_customer",
        data={
            "email": "ada@example.com",
            "name": "Ada Lovelace",
            "metadata[tier]": "gold",
        },
    )


def test_stripe_create_checkout_session_v2_flattens_line_items(monkeypatch) -> None:
    transport = _mock_transport({"id": "cs_123"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = registry.get("stripe_create_checkout_session_v2").func(
        input=None,
        credentials={"api_key": "sk_test"},
        success_url="https://example.test/success",
        cancel_url="https://example.test/cancel",
        price_id="price_123",
        quantity=2,
        allow_promotion_codes=True,
        metadata={"order_id": "ord_123"},
    )

    assert result == {"id": "cs_123"}
    transport.request.assert_called_once_with(
        "POST",
        "/checkout/sessions",
        operation="create_checkout_session",
        data={
            "success_url": "https://example.test/success",
            "cancel_url": "https://example.test/cancel",
            "mode": "payment",
            "line_items[0][price]": "price_123",
            "line_items[0][quantity]": 2,
            "allow_promotion_codes": "true",
            "metadata[order_id]": "ord_123",
        },
    )


def test_stripe_create_payment_intent_v2_payload(monkeypatch) -> None:
    transport = _mock_transport({"id": "pi_123"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("stripe_create_payment_intent_v2").func(
        input={"metadata": {"order_id": "ord_123"}},
        credentials={"api_key": "sk_test"},
        amount=2000,
        currency="aud",
        confirm=True,
    )

    transport.request.assert_called_once_with(
        "POST",
        "/payment_intents",
        operation="create_payment_intent",
        data={
            "amount": 2000,
            "currency": "aud",
            "confirm": "true",
            "automatic_payment_methods[enabled]": "true",
            "metadata[order_id]": "ord_123",
        },
    )


def test_stripe_create_refund_v2_requires_payment_reference(monkeypatch) -> None:
    transport = _mock_transport({"id": "re_123"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("stripe_create_refund_v2").func(
        input=None,
        credentials={"api_key": "sk_test"},
        payment_intent="pi_123",
        amount=500,
        reason="requested_by_customer",
    )

    transport.request.assert_called_once_with(
        "POST",
        "/refunds",
        operation="create_refund",
        data={
            "payment_intent": "pi_123",
            "amount": 500,
            "reason": "requested_by_customer",
        },
    )


def test_stripe_invoice_and_subscription_nodes(monkeypatch) -> None:
    transport = _mock_transport({"id": "obj_123"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("stripe_list_payment_intents_v2").func(
        input=None,
        credentials={"api_key": "sk_test"},
        customer_id="cus_123",
        limit=2,
    )
    registry.get("stripe_create_invoice_v2").func(
        input=None,
        credentials={"api_key": "sk_test"},
        customer_id="cus_123",
        collection_method="send_invoice",
        days_until_due=7,
        auto_advance=True,
    )
    registry.get("stripe_create_subscription_v2").func(
        input=None,
        credentials={"api_key": "sk_test"},
        customer_id="cus_123",
        price_id="price_123",
        quantity=3,
    )
    registry.get("stripe_cancel_subscription_v2").func(
        input=None,
        credentials={"api_key": "sk_test"},
        subscription_id="sub_123",
        invoice_now=True,
        prorate=False,
    )

    assert transport.request.call_args_list[0].args == ("GET", "/payment_intents")
    assert transport.request.call_args_list[0].kwargs == {
        "operation": "list_payment_intents",
        "params": {"limit": 2, "customer": "cus_123"},
    }
    assert transport.request.call_args_list[1].args == ("POST", "/invoices")
    assert transport.request.call_args_list[1].kwargs == {
        "operation": "create_invoice",
        "data": {
            "customer": "cus_123",
            "collection_method": "send_invoice",
            "days_until_due": 7,
            "auto_advance": "true",
        },
    }
    assert transport.request.call_args_list[2].args == ("POST", "/subscriptions")
    assert transport.request.call_args_list[2].kwargs == {
        "operation": "create_subscription",
        "data": {
            "customer": "cus_123",
            "items[0][price]": "price_123",
            "items[0][quantity]": 3,
            "payment_behavior": "default_incomplete",
        },
    }
    assert transport.request.call_args_list[3].args == ("DELETE", "/subscriptions/sub_123")
    assert transport.request.call_args_list[3].kwargs == {
        "operation": "cancel_subscription",
        "data": {"invoice_now": "true", "prorate": "false"},
    }
