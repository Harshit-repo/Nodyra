"""Smoke test — every new HTTP-wrapper node registers with a sane manifest.

We don't call the real APIs (no keys / network), but we make sure the node
shows up in the registry, has the right category, has a brand-icon name,
and that calling it with empty creds raises the expected validation error.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.sdk import registry
from noodle_nodes import ai_extra, cloud_devops, communication, llm, saas, storage

BRAND_NODE_IDS = {
    # Communication (Integrations)
    "pushover_notify": ("brand:pushover", "Integrations"),
    # SaaS (Integrations)
    "linear_create_issue": ("brand:linear", "Integrations"),
    "jira_create_issue": ("brand:jira", "Integrations"),
    # AI
    "openai_embeddings": ("brand:openai", "AI"),
    "openai_whisper_transcribe": ("brand:openai", "AI"),
    "openai_tts": ("brand:openai", "AI"),
    "cohere_embed": ("brand:cohere", "AI"),
    "deepl_translate": ("brand:deepl", "AI"),
    "pinecone_upsert": ("brand:pinecone", "AI"),
    "pinecone_query": ("brand:pinecone", "AI"),
    "ai_batch_embeddings": ("brand:openai", "AI"),
    "ai_vector_retriever": ("brand:pinecone", "AI"),
    # Storage / DB (Integrations)
    "mongodb_query": ("brand:mongodb", "Integrations"),
    "redis_command": ("brand:redis", "Integrations"),
    "elasticsearch_search": ("brand:elasticsearch", "Integrations"),
    "gcs_upload": ("brand:googlecloud", "Integrations"),
    "gcs_list_objects": ("brand:googlecloud", "Integrations"),
    "azure_blob_upload": ("brand:microsoftazure", "Integrations"),
    "dynamodb_get_item": ("brand:amazondynamodb", "Integrations"),
    "dynamodb_put_item": ("brand:amazondynamodb", "Integrations"),
    # AWS S3 (Integrations)
    "s3_put_object": ("brand:amazons3", "Integrations"),
    "s3_get_object": ("brand:amazons3", "Integrations"),
    # Cloud / DevOps (Integrations + System)
    "aws_lambda_invoke": ("brand:awslambda", "Integrations"),
    "aws_sqs_send": ("brand:amazonsqs", "Integrations"),
    "aws_sqs_receive": ("brand:amazonsqs", "Integrations"),
    "aws_sns_publish": ("brand:amazonsns", "Integrations"),
    "git_clone": ("brand:git", "System"),
    "git_pull": ("brand:git", "System"),
}


def test_brand_nodes_registered_with_brand_icon() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    for node_id, (expected_icon, expected_category) in BRAND_NODE_IDS.items():
        manifest = manifests.get(node_id)
        assert manifest is not None, f"{node_id} not registered"
        assert manifest.icon == expected_icon, (
            f"{node_id}: icon={manifest.icon!r} expected {expected_icon!r}"
        )
        assert manifest.category == expected_category, (
            f"{node_id}: category={manifest.category!r} "
            f"expected {expected_category!r}"
        )


@pytest.mark.parametrize(
    "func",
    [
        # Communication
        communication.pushover_notify,
        # SaaS
        saas.jira_create_issue,
        saas.linear_create_issue,
        # AI/ML
        ai_extra.openai_embeddings,
        ai_extra.openai_tts,
        ai_extra.cohere_embed,
        ai_extra.deepl_translate,
        ai_extra.pinecone_upsert,
        ai_extra.pinecone_query,
        llm.ai_chat,
        llm.ai_structured_output,
        llm.ai_batch_embeddings,
        llm.ai_dataset_map,
        llm.ai_vector_retriever,
        llm.ai_rag_answer,
        llm.ai_agent,
        llm.ai_moderation_guard,
        llm.ai_vision_analyze,
        llm.ai_image_generate,
        # Storage / DB
        storage.mongodb_query,
        storage.redis_command,
        storage.elasticsearch_search,
        storage.gcs_upload,
        storage.gcs_list_objects,
        storage.azure_blob_upload,
        storage.dynamodb_get_item,
        storage.dynamodb_put_item,
        # Cloud / DevOps
        cloud_devops.aws_lambda_invoke,
        cloud_devops.aws_sqs_send,
        cloud_devops.aws_sqs_receive,
        cloud_devops.aws_sns_publish,
        cloud_devops.ssh_execute,
        cloud_devops.git_clone,
        cloud_devops.git_pull,
    ],
)
def test_node_with_empty_required_args_raises_value_error(func: Any) -> None:
    """Calling a node with all blank required fields should fail fast with
    a ValueError rather than make a doomed HTTP request."""
    # Build a call where every str-defaulted required-looking param is blank.
    # The decorator wraps these, so call the underlying function directly via
    # its signature defaults.
    sig = inspect.signature(func)
    kwargs: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if name == "input":
            kwargs[name] = None
        elif param.default is inspect.Parameter.empty:
            kwargs[name] = ""
    with pytest.raises(ValueError):
        if inspect.iscoroutinefunction(func):
            import asyncio

            asyncio.run(func(**kwargs))
        else:
            func(**kwargs)
