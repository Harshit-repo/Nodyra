"""Curated workflow templates: list and instantiate."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import nodyra_nodes  # noqa: F401 - registers built-in nodes
from app.db import get_session
from app.models import Environment, User, Workflow, WorkflowVersion
from app.security import optional_current_user, require_permission
from app.services.audit import log_audit
from app.services.github_sync import enqueue_github_push
from app.services.github_sync_jobs import notify_sync_workers
from app.services.workflow_events import WORKFLOW_CREATED, publish_workflow_event
from nodyra.engine.types import GraphError
from nodyra.engine.validation import _validate_graph
from nodyra.models import WorkflowGraph
from nodyra.sdk import registry as node_registry

router = APIRouter(tags=["templates"])
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"


class TemplateSummary(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)


class InstantiateTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def trim_name(self) -> InstantiateTemplateRequest:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name cannot be blank")
        return self


class InstantiateTemplateResponse(BaseModel):
    id: str
    name: str


@lru_cache(maxsize=1)
def _load_templates() -> dict[str, dict[str, Any]]:
    templates: dict[str, dict[str, Any]] = {}
    for path in sorted(_TEMPLATE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        graph = WorkflowGraph.model_validate(data["graph"])
        try:
            _validate_graph(graph, node_registry)
        except GraphError as exc:
            raise RuntimeError(f"Template {path.name} is invalid: {exc}") from exc
        templates[str(data["id"])] = data
    return templates


async def _global_env_id(session: AsyncSession) -> str | None:
    return await session.scalar(
        select(Environment.id).where(Environment.is_global.is_(True)).limit(1)
    )


@router.get("/templates", response_model=list[TemplateSummary])
async def list_templates() -> list[TemplateSummary]:
    return [
        TemplateSummary(
            id=str(template["id"]),
            name=str(template["name"]),
            description=str(template["description"]),
            tags=list(template.get("tags") or []),
        )
        for template in _load_templates().values()
    ]


@router.post(
    "/templates/{template_id}/instantiate",
    response_model=InstantiateTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def instantiate_template(
    template_id: str,
    body: InstantiateTemplateRequest,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
) -> InstantiateTemplateResponse:
    template = _load_templates().get(template_id)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown template")

    graph = WorkflowGraph.model_validate(template["graph"]).model_dump(mode="json")
    workflow = Workflow(
        name=body.name,
        environment_id=await _global_env_id(session),
        draft_graph=graph,
        published_version=1,
    )
    workflow.versions.append(WorkflowVersion(version=1, graph=graph))
    session.add(workflow)
    await log_audit(
        session,
        "create",
        "workflow",
        detail=body.name,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await enqueue_github_push(session, workflow, "ui")
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Workflow conflicts with an existing tenant-scoped value.",
        ) from exc
    notify_sync_workers()
    publish_workflow_event(
        workflow,
        WORKFLOW_CREATED,
        origin="ui",
        operation="create_from_template",
        actor=actor,
    )
    return InstantiateTemplateResponse(id=workflow.id, name=workflow.name)
