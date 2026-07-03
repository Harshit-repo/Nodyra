"""MCP resource catalogue — exposes Nodyra data as readable context."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
                "uri": "nodyra://node-types",
                "name": "Node Type Catalogue",
                "description": "All available node types with ids, categories, and descriptions.",
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
        }
    ]


async def read_resource(session: AsyncSession, uri: str) -> dict[str, Any]:
    """Return {uri, mimeType, text}. Raises ValueError for unknown URIs."""
    if uri == "nodyra://node-types":
        types = [
            {
                "id": m.id,
                "name": m.name,
                "category": m.category,
                "description": m.description,
            }
            for m in node_registry.manifests()
            if not m.hidden and not m.deprecated
        ]
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps({"node_types": types}, ensure_ascii=False),
        }

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
