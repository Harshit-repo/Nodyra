import io
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import Environment, Workflow
from noodle_exporter import docker_bundle, slugify, workflow_to_script

router = APIRouter(tags=["export"])

EMPTY_GRAPH: dict = {"nodes": [], "edges": []}


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
    script = workflow_to_script(graph, workflow.name)
    return Response(
        script,
        media_type="text/x-python",
        headers={
            "Content-Disposition": f'attachment; filename="{slugify(workflow.name)}.py"'
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

    bundle = docker_bundle(
        graph, workflow.name, python_version=python_version, packages=packages
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
