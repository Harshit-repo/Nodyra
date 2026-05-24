"""User code modules: upload-to-nodes.

Each row is a Python file whose top-level functions register as nodes via
``noodle.sdk.register_module_functions``. v1 wires only the per-workflow
scope (the runner gathers a workflow's modules and passes them with each
run). Global / per-environment scopes are persisted but not yet loaded
into warm runtime processes.
"""

import ast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import CodeModule
from app.schemas import (
    CodeModuleCreate,
    CodeModuleFunctionPreview,
    CodeModuleInfo,
    CodeModuleUpdate,
)
from app.services.audit import log_audit
from noodle.models import NodeManifest
from noodle.sdk import NodeRegistry, register_module_functions

router = APIRouter(prefix="/code-modules", tags=["code-modules"])


async def _load(session: AsyncSession, module_id: str) -> CodeModule:
    module = await session.get(CodeModule, module_id)
    if module is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Code module not found")
    return module


@router.get("", response_model=list[CodeModuleInfo])
async def list_code_modules(
    scope: str | None = None,
    workflow_id: str | None = None,
    environment_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(CodeModule).order_by(CodeModule.created_at.desc())
    if scope is not None:
        stmt = stmt.where(CodeModule.scope == scope)
    if workflow_id is not None:
        stmt = stmt.where(CodeModule.workflow_id == workflow_id)
    if environment_id is not None:
        stmt = stmt.where(CodeModule.environment_id == environment_id)
    result = await session.scalars(stmt)
    return list(result.all())


@router.post("", response_model=CodeModuleInfo, status_code=status.HTTP_201_CREATED)
async def create_code_module(
    body: CodeModuleCreate, session: AsyncSession = Depends(get_session)
):
    if body.scope == "workflow" and not body.workflow_id:
        raise HTTPException(400, "workflow_id is required for scope=workflow")
    if body.scope == "environment" and not body.environment_id:
        raise HTTPException(400, "environment_id is required for scope=environment")

    module = CodeModule(
        scope=body.scope,
        workflow_id=body.workflow_id if body.scope == "workflow" else None,
        environment_id=body.environment_id if body.scope == "environment" else None,
        name=body.name,
        contents=body.contents or "",
    )
    session.add(module)
    await log_audit(session, "create", "code_module", detail=body.name)
    await session.commit()
    await session.refresh(module)
    return module


@router.get("/{module_id}", response_model=CodeModuleInfo)
async def get_code_module(
    module_id: str, session: AsyncSession = Depends(get_session)
):
    return await _load(session, module_id)


@router.put("/{module_id}", response_model=CodeModuleInfo)
async def update_code_module(
    module_id: str,
    body: CodeModuleUpdate,
    session: AsyncSession = Depends(get_session),
):
    module = await _load(session, module_id)
    if body.name is not None:
        module.name = body.name
    if body.contents is not None:
        module.contents = body.contents
    await session.commit()
    await session.refresh(module)
    return module


@router.delete("/{module_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_code_module(
    module_id: str, session: AsyncSession = Depends(get_session)
):
    module = await _load(session, module_id)
    await log_audit(session, "delete", "code_module", module.id, module.name)
    await session.delete(module)
    await session.commit()


def _preview(module_id: str, source: str) -> CodeModuleFunctionPreview:
    """Parse + register into a throwaway registry to surface what's exposed."""
    if not source.strip():
        return CodeModuleFunctionPreview()
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return CodeModuleFunctionPreview(
            syntax_error=f"line {exc.lineno}: {exc.msg}"
        )
    sandbox = NodeRegistry()
    try:
        registered, skipped = register_module_functions(module_id, source, sandbox)
    except Exception as exc:  # noqa: BLE001 - bad user code surfaces in the preview
        return CodeModuleFunctionPreview(syntax_error=f"{type(exc).__name__}: {exc}")
    return CodeModuleFunctionPreview(
        registered=registered,
        skipped=[{"name": name, "reason": reason} for name, reason in skipped],
    )


@router.get("/{module_id}/preview", response_model=CodeModuleFunctionPreview)
async def preview_code_module(
    module_id: str, session: AsyncSession = Depends(get_session)
):
    module = await _load(session, module_id)
    return _preview(module.id, module.contents)


@router.get("/manifests/workflow/{workflow_id}", response_model=list[NodeManifest])
async def workflow_custom_node_manifests(
    workflow_id: str, session: AsyncSession = Depends(get_session)
):
    """Manifests the editor needs to render the palette's Custom group.

    Built-in nodes already come from /nodes; this endpoint adds the
    workflow-scoped function nodes on top.
    """
    rows = (
        await session.scalars(
            select(CodeModule).where(CodeModule.workflow_id == workflow_id)
        )
    ).all()
    manifests: list[NodeManifest] = []
    for module in rows:
        if not module.contents.strip():
            continue
        sandbox = NodeRegistry()
        try:
            register_module_functions(module.id, module.contents, sandbox)
        except Exception:  # noqa: BLE001 - bad code → no manifests, but other modules still work
            continue
        manifests.extend(sandbox.manifests())
    return manifests
