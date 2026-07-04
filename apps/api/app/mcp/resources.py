"""MCP resource catalogue — exposes Nodyra data as readable context."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.mcp.guidance import compact_node_contract, workflow_authoring_guide
from app.models import Workflow
from nodyra.sdk import registry as node_registry

RESOURCE_PAGE_SIZE = 50


async def list_resources(
    session: AsyncSession, *, offset: int = 0
) -> tuple[list[dict[str, Any]], bool]:
    resources: list[dict[str, Any]] = []
    if offset == 0:
        resources.append(
            {
                "uri": "nodyra://workflow-authoring-guide",
                "name": "Workflow Authoring Guide",
                "description": (
                    "LLM-oriented guide for creating, validating, testing, "
                    "publishing, scheduling, and exposing Nodyra workflows."
                ),
                "mimeType": "application/json",
            }
        )
        resources.append(
            {
                "uri": "nodyra://node-types",
                "name": "Node Type Catalogue",
                "description": (
                    "All available node types with ids, categories, descriptions, "
                    "and compact LLM guidance."
                ),
                "mimeType": "application/json",
            }
        )
    workflows = list((
        await session.scalars(
            select(Workflow)
            .order_by(Workflow.updated_at.desc(), Workflow.id)
            .offset(max(0, offset))
            .limit(RESOURCE_PAGE_SIZE + 1)
        )
    ).all())
    has_more = len(workflows) > RESOURCE_PAGE_SIZE
    for wf in workflows[:RESOURCE_PAGE_SIZE]:
        resources.append(
            {
                "uri": f"nodyra://workflow/{wf.id}",
                "name": wf.name,
                "description": f"Workflow graph and metadata for '{wf.name}'.",
                "mimeType": "application/json",
            }
        )
    return resources, has_more


def list_resource_templates() -> list[dict[str, Any]]:
    return [
        {
            "uriTemplate": "nodyra://workflow/{workflow_id}",
            "name": "Workflow",
            "description": "A workflow's current graph and metadata by id.",
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "nodyra://node-type/{node_type}",
            "name": "Node Type Contract",
            "description": "A compact LLM-ready contract for one node type.",
            "mimeType": "application/json",
        },
    ]


async def read_resource(session: AsyncSession, uri: str) -> dict[str, Any]:
    """Return {uri, mimeType, text}. Raises ValueError for unknown URIs."""
    if uri == "nodyra://workflow-authoring-guide":
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(
                workflow_authoring_guide(detail="full"),
                ensure_ascii=False,
                default=str,
            ),
        }

    if uri == "nodyra://node-types":
        types = [
            compact_node_contract(m, include_examples=False)
            for m in node_registry.manifests()
            if not m.hidden and not m.deprecated
        ]
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps({"node_types": types}, ensure_ascii=False),
        }

    if uri.startswith("nodyra://node-type/"):
        node_type = uri[len("nodyra://node-type/"):]
        for manifest in node_registry.manifests():
            if manifest.id == node_type and not manifest.hidden:
                return {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(
                        compact_node_contract(manifest, include_examples=True),
                        ensure_ascii=False,
                        default=str,
                    ),
                }
        raise ValueError(f"Resource not found: {uri!r}")

    if uri.startswith("nodyra://workflow/"):
        workflow_id = uri[len("nodyra://workflow/"):]
        workflow = await session.get(
            Workflow,
            workflow_id,
            options=[selectinload(Workflow.versions)],
            populate_existing=True,
        )
        if workflow is None:
            raise ValueError(f"Resource not found: {uri!r}")
        graph = workflow.draft_graph
        if graph is None and workflow.versions:
            graph = workflow.versions[-1].graph or {"nodes": [], "edges": []}
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(
                {
                    "id": workflow.id,
                    "name": workflow.name,
                    "active": workflow.active,
                    "published_version": workflow.published_version,
                    "graph": graph or {"nodes": [], "edges": []},
                },
                ensure_ascii=False,
                default=str,
            ),
        }

    raise ValueError(f"Unknown resource URI: {uri!r}")
