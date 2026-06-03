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
    manifest = manifests["stripe_create_customer_v2"]

    assert manifest.name == "Stripe Create Customer V2"
    assert manifest.category == "Integrations"
    params = {param.name: param for param in manifest.params}
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
