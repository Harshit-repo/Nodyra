import io
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Response, status
from noodle_importer import import_module
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import noodle_nodes  # noqa: F401 - registers built-in nodes
from app.db import get_session
from app.models import Environment, Workflow, WorkflowVersion
from app.security import optional_current_user, require_permission
from app.services.audit import log_audit
from noodle.models import WorkflowGraph
from noodle.sdk import registry as node_registry
from noodle_exporter import docker_bundle, slugify, workflow_to_module, workflow_to_script

router = APIRouter(tags=["export"])


class WorkflowImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source: str = Field(description="Content of a .module.py export file")


class WorkflowImportResponse(BaseModel):
    workflow_id: str
    name: str

EMPTY_GRAPH: dict = {"nodes": [], "edges": []}

# A3: node types whose params reference another workflow, and the param key
# holding the referenced workflow id. Exports bundle these graphs so the
# generated script resolves workflow calls locally.
_SUBWORKFLOW_PARAM_KEYS: dict[str, str] = {
    "execute_workflow": "workflow_id",
    "map_items": "workflow_id",
    "map_dataset": "workflow_id",
    "map_group": "child_workflow_id",
}


def _referenced_workflow_ids(graph: dict) -> set[str]:
    out: set[str] = set()
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        key = _SUBWORKFLOW_PARAM_KEYS.get(str(node.get("type") or ""))
        if key:
            wid = str((node.get("params") or {}).get(key) or "")
            if wid:
                out.add(wid)
    return out


async def _collect_subworkflows(
    session: AsyncSession, graph: dict
) -> dict[str, dict]:
    """Every workflow graph reachable from ``graph`` via sub-workflow params.

    Unresolvable ids are skipped — the generated script raises a clear
    "not bundled" error if the node actually fires. The visited set guards
    against reference cycles (the runtime engine would refuse them anyway).
    """
    out: dict[str, dict] = {}
    pending = _referenced_workflow_ids(graph)
    while pending:
        wid = pending.pop()
        if wid in out:
            continue
        workflow = await session.get(
            Workflow, wid, options=[selectinload(Workflow.versions)]
        )
        if workflow is None:
            continue
        child = (
            workflow.draft_graph
            or (workflow.versions[-1].graph if workflow.versions else None)
            or EMPTY_GRAPH
        )
        out[wid] = child
        pending |= _referenced_workflow_ids(child) - set(out)
    return out


async def _load(session: AsyncSession, workflow_id: str) -> Workflow:
    workflow = await session.get(
        Workflow, workflow_id, options=[selectinload(Workflow.versions)]
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return workflow


@router.get("/workflows/{workflow_id}/export.py")
async def export_script(
    workflow_id: str, session: AsyncSession = Depends(get_session)
) -> Response:
    workflow = await _load(session, workflow_id)
    graph = workflow.draft_graph or workflow.versions[-1].graph or EMPTY_GRAPH
    subs = await _collect_subworkflows(session, graph)
    script = workflow_to_script(
        graph, workflow.name, subworkflows=subs, root_id=workflow.id
    )
    return Response(
        script,
        media_type="text/x-python",
        headers={
            "Content-Disposition": f'attachment; filename="{slugify(workflow.name)}.py"'
        },
    )


@router.get("/workflows/{workflow_id}/export.module.py")
async def export_module(
    workflow_id: str, session: AsyncSession = Depends(get_session)
) -> Response:
    """Code-first export: one @node-decorated function per workflow node."""
    workflow = await _load(session, workflow_id)
    graph = workflow.draft_graph or workflow.versions[-1].graph or EMPTY_GRAPH
    subs = await _collect_subworkflows(session, graph)
    script = workflow_to_module(
        WorkflowGraph.model_validate(graph),
        workflow.name,
        registry=node_registry,
        subworkflows=subs,
        root_id=workflow.id,
    )
    return Response(
        script,
        media_type="text/x-python",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{slugify(workflow.name)}_module.py"'
            )
        },
    )


@router.get("/workflows/{workflow_id}/export/docker")
async def export_docker(
    workflow_id: str, session: AsyncSession = Depends(get_session)
) -> Response:
    workflow = await _load(session, workflow_id)
    graph = workflow.draft_graph or workflow.versions[-1].graph or EMPTY_GRAPH

    packages: list[str] = []
    python_version = "3.12"
    if workflow.environment_id:
        env = await session.get(Environment, workflow.environment_id)
        if env is not None:
            packages = list(env.packages)
            python_version = env.python_version

    subs = await _collect_subworkflows(session, graph)
    bundle = docker_bundle(
        graph, workflow.name, python_version=python_version, packages=packages,
        subworkflows=subs, root_id=workflow.id,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, content in bundle.items():
            archive.writestr(filename, content)

    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{slugify(workflow.name)}-docker.zip"'
            )
        },
    )


@router.post(
    "/import",
    response_model=WorkflowImportResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def import_workflow(
    body: WorkflowImportRequest,
    session: AsyncSession = Depends(get_session),
    actor=Depends(optional_current_user),
) -> WorkflowImportResponse:
    """T-10: Import a workflow from a .module.py export (backup/restore and template sharing).

    Parses the source using the AST-based importer — no code is executed.
    Creates a new workflow with the reconstructed graph as the draft, with
    version 1 pinned from the same graph.
    """
    try:
        graph: WorkflowGraph = import_module(body.source)
    except ImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    graph_dict = graph.model_dump()
    env = await session.scalar(select(Environment).where(Environment.is_global.is_(True)).limit(1))
    env_id: str | None = env.id if env is not None else None

    workflow = Workflow(
        name=body.name,
        environment_id=env_id,
        draft_graph=graph_dict,
        published_version=1,
    )
    workflow.versions.append(WorkflowVersion(version=1, graph=graph_dict))
    session.add(workflow)
    await log_audit(
        session,
        "import",
        "workflow",
        detail=body.name,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await session.commit()
    return WorkflowImportResponse(workflow_id=workflow.id, name=workflow.name)
