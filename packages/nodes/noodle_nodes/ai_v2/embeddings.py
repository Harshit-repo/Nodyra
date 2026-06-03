"""AI Embedding Model supplier nodes — typed, WP10."""

from __future__ import annotations

from noodle.ai_runtime import EmbeddingModelAdapter
from noodle.sdk import node
from noodle_nodes._creds import cred_multi
from noodle_nodes.ai_v2.providers.embeddings import embedding_adapter_from_credentials

AI_CATEGORY = "AI"

EMBEDDING_CREDENTIAL_FIELDS = [
    "provider",
    "api_key",
    "base_url",
    "prompt_price_per_1m_tokens",
]

EMBEDDING_PROVIDER_CHOICES = ["openai", "openai_compatible", "ollama", "cohere"]

EMBEDDING_MODEL_CHOICES = [
    "text-embedding-3-small",
    "text-embedding-3-large",
    "nomic-embed-text",
    "mxbai-embed-large",
    "embed-english-v3.0",
    "embed-multilingual-v3.0",
]


@node(
    name="AI Embedding Model",
    id="ai_embedding_model",
    category=AI_CATEGORY,
    role="supplier",
    icon="ai",
    outputs=["model"],
    output_kinds={"model": "ai_embedding_model"},
    param_groups={"Options": ["prompt_price_per_1m_tokens"]},
    params={
        "credentials": {
            **cred_multi(
                "embedding_provider",
                "Embedding provider credential",
                EMBEDDING_CREDENTIAL_FIELDS,
            ),
            "description": "Credential for the selected embedding provider.",
        },
        "provider": {
            "choices": EMBEDDING_PROVIDER_CHOICES,
            "description": "Embedding provider.",
        },
        "model": {
            "choices": EMBEDDING_MODEL_CHOICES,
            "placeholder": "text-embedding-3-small",
            "description": "Embedding model ID.",
        },
        "prompt_price_per_1m_tokens": {
            "description": "Optional embedding-token price in USD per 1M tokens.",
            "group": "Options",
        },
    },
)
def ai_embedding_model(
    credentials: dict | None = None,
    provider: str = "openai",
    model: str = "text-embedding-3-small",
    prompt_price_per_1m_tokens: float | None = None,
) -> EmbeddingModelAdapter:
    """Supply an embedding model adapter to downstream RAG / vector nodes."""
    return embedding_adapter_from_credentials(
        credentials or {},
        provider=provider,
        model=model,
        prompt_price_per_1m_tokens=prompt_price_per_1m_tokens,
    )
