from typing import Any
from unittest.mock import MagicMock

import pytest

import nodyra_nodes  # noqa: F401 - importing registers provider nodes
from nodyra.sdk import registry
from nodyra_nodes.integrations_v2.providers.huggingface import operations
from nodyra_nodes.integrations_v2.registry import execute_registered_operation


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def _run(resource: str, operation: str, **kwargs: Any) -> Any:
    return registry.get("huggingface").func(
        resource=resource,
        operation=operation,
        **kwargs,
    )


def test_huggingface_v2_node_is_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}

    assert "huggingface_text_generation_v2" not in manifests
    assert "huggingface_embeddings_v2" not in manifests

    node = manifests["huggingface"]
    assert node.name == "HuggingFace"
    assert node.icon == "brand:huggingface"
    assert node.category == "Integrations"
    assert node.usable_as_tool is True
    assert node.integration is not None

    resources = {r.id: [op.id for op in r.operations] for r in node.integration.resources}
    assert resources == {"inference": ["text_generation", "embeddings"]}

    params = {param.name: param for param in node.params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "huggingface_api"
    assert params["credentials"].credential.multi is True
    assert params["model"].required is True
    assert params["prompt"].multiline is True
    assert params["input_text"].multiline is True
    assert params["router_provider"].advanced is True


def test_huggingface_text_generation_dispatches_chat_completion(monkeypatch) -> None:
    transport = _mock_transport(
        {"choices": [{"message": {"content": "Generated reply"}}]}
    )
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = execute_registered_operation(
        "huggingface_text_generation_v2",
        input=None,
        credentials={"api_token": "hf_test"},
        model="meta-llama/Llama-3.1-8B-Instruct",
        prompt="hi",
        router_provider="hf-inference",
        max_tokens=64,
        temperature=0.2,
    )

    assert result == {
        "text": "Generated reply",
        "raw": {"choices": [{"message": {"content": "Generated reply"}}]},
    }
    transport.request.assert_called_once_with(
        "POST",
        "/v1/chat/completions",
        operation="text_generation",
        json_body={
            "model": "meta-llama/Llama-3.1-8B-Instruct:hf-inference",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 64,
            "temperature": 0.2,
        },
    )


def test_huggingface_embeddings_dispatches_feature_extraction(monkeypatch) -> None:
    transport = _mock_transport([[0.1, 0.2, 0.3]])
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = execute_registered_operation(
        "huggingface_embeddings_v2",
        input=None,
        credentials={"api_token": "hf_test"},
        model="sentence-transformers/all-MiniLM-L6-v2",
        input_text="embed me",
        normalize=True,
        truncate=False,
    )

    assert result == {"embedding": [0.1, 0.2, 0.3], "raw": [[0.1, 0.2, 0.3]]}
    transport.request.assert_called_once_with(
        "POST",
        "/hf-inference/models/sentence-transformers/all-MiniLM-L6-v2",
        operation="embeddings",
        json_body={"inputs": "embed me", "normalize": True, "truncate": False},
    )


def test_huggingface_consolidated_node_runs_executor(monkeypatch) -> None:
    transport = _mock_transport({"data": [{"embedding": [1.0, 2.0]}]})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = _run(
        "inference",
        "embeddings",
        input=None,
        credentials={"api_token": "hf_test"},
        model="sentence-transformers/all-MiniLM-L6-v2",
        input_text="embed me",
    )

    assert result["embedding"] == [1.0, 2.0]


def test_huggingface_requires_api_token() -> None:
    with pytest.raises(ValueError, match="api_token"):
        operations.text_generation(
            credentials={},
            model="Qwen/Qwen3-235B-A22B",
            prompt="hi",
        )
