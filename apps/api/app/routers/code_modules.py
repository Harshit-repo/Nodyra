"""User code modules: upload-to-nodes.

Each row is a Python file whose top-level functions appear as nodes. API
preview and palette manifest generation use static AST discovery, so uploaded
code is not executed in the API process. Actual workflow runs register the
module source in the runner/runtime environment.
"""

import ast
import sys

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import CodeModule, Environment, User, Workflow
from app.schemas import (
    CodeModuleCreate,
    CodeModuleFunctionPreview,
    CodeModuleFunctionShape,
    CodeModuleInfo,
    CodeModuleUpdate,
)
from app.security import optional_current_user, require_permission
from app.services.audit import log_audit
from app.services.starter_graph import build_starter_graph
from noodle.models import NodeManifest
from noodle.sdk import discover_module_function_manifests, discover_module_nodes

# A small import-name → pip-name map for common quirks. Anything not in here
# falls back to assuming the pip name matches the import name (true for
# most packages: requests, pandas, numpy, etc.).
_KNOWN_IMPORT_TO_PIP: dict[str, str] = {
    "cv2": "opencv-python",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
}


def _extract_top_level_imports(source: str) -> list[str]:
    """Return the top-level imported top-level module names.

    Stdlib modules and relative imports are filtered out so the result is
    the set of third-party imports the user might actually need to install.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    seen: list[str] = []
    stdlib = sys.stdlib_module_names
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top and top not in stdlib and top not in seen:
                    seen.append(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # relative import — internal to the file
            if not node.module:
                continue
            top = node.module.split(".", 1)[0]
            if top and top not in stdlib and top not in seen:
                seen.append(top)
    return seen


def _missing_in_env(imports: list[str], env: Environment | None) -> list[str]:
    """Subset of imports that aren't satisfied by the env's installed packages.

    Compares the *pip* name (translated from the import name via the small
    known-quirks map) against the env's ``packages`` list, since that's what
    the Environments UI installs.
    """
    if env is None:
        return list(imports)
    installed = {str(p).lower() for p in (env.packages or [])}
    missing: list[str] = []
    for name in imports:
        pip_name = _KNOWN_IMPORT_TO_PIP.get(name, name)
        if pip_name.lower() in installed:
            continue
        # Some packages register themselves under a normalized name (e.g.
        # 'python-dateutil' → 'dateutil'); also accept the import name match.
        if name.lower() in installed:
            continue
        missing.append(pip_name)
    return missing

router = APIRouter(prefix="/code-modules", tags=["code-modules"])


async def _load(session: AsyncSession, module_id: str) -> CodeModule:
    # populate_existing=True forces a real SELECT so the org-filter hook fires
    # even when the row is already in the session identity map — same
    # cross-tenant defence as credentials._load (R-1/R-11). CodeModule is
    # org-scoped and the GET/preview routes have no extra permission gate.
    module = await session.get(CodeModule, module_id, populate_existing=True)
    if module is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Code module not found")
    return module


@router.get("", response_model=list[CodeModuleInfo])
async def list_code_modules(
    scope: str | None = None,
    workflow_id: str | None = None,
    environment_id: str | None = None,
    visible_to_workflow: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """List code modules, optionally narrowed to one scope or one workflow.

    ``visible_to_workflow`` returns the union the runner gathers: global
    modules + the workflow's env modules + the workflow's own modules. Used
    by the editor Functions panel.
    """
    from sqlalchemy import or_

    stmt = select(CodeModule).order_by(CodeModule.created_at.desc())
    if visible_to_workflow is not None:
        workflow = await session.get(Workflow, visible_to_workflow)
        env_id = workflow.environment_id if workflow else None
        stmt = stmt.where(
            or_(
                CodeModule.scope == "global",
                CodeModule.workflow_id == visible_to_workflow,
                (
                    (CodeModule.scope == "environment")
                    & (CodeModule.environment_id == env_id)
                )
                if env_id
                else CodeModule.id.is_(None),
            )
        )
    if scope is not None:
        stmt = stmt.where(CodeModule.scope == scope)
    if workflow_id is not None:
        stmt = stmt.where(CodeModule.workflow_id == workflow_id)
    if environment_id is not None:
        stmt = stmt.where(CodeModule.environment_id == environment_id)
    result = await session.scalars(stmt)
    return list(result.all())


@router.post(
    "",
    response_model=CodeModuleInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("code_module:write"))],
)
async def create_code_module(
    body: CodeModuleCreate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
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
        include_undecorated=body.include_undecorated,
    )
    session.add(module)
    await log_audit(session, "create", "code_module", detail=body.name,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.commit()
    await session.refresh(module)
    return module


@router.get("/{module_id}", response_model=CodeModuleInfo)
async def get_code_module(
    module_id: str, session: AsyncSession = Depends(get_session)
):
    return await _load(session, module_id)


@router.put(
    "/{module_id}",
    response_model=CodeModuleInfo,
    dependencies=[Depends(require_permission("code_module:write"))],
)
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
    if body.include_undecorated is not None:
        module.include_undecorated = body.include_undecorated
    await session.commit()
    await session.refresh(module)
    return module


@router.delete(
    "/{module_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("code_module:write"))],
)
async def delete_code_module(
    module_id: str,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    module = await _load(session, module_id)
    await log_audit(session, "delete", "code_module", module.id, module.name,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.delete(module)
    await session.commit()


async def _workflow_environment(
    session: AsyncSession, workflow_id: str | None
) -> Environment | None:
    if workflow_id is None:
        return None
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None or workflow.environment_id is None:
        return None
    return await session.get(Environment, workflow.environment_id)


def _preview_payload(
    module_id: str, source: str, env: Environment | None, *, include_undecorated: bool
) -> CodeModuleFunctionPreview:
    """Parse the module statically to surface which functions become nodes.

    Also walks the AST for top-level imports and reports which of those
    aren't installed in ``env``'s packages list, so the UI can prompt the
    user to install them with one click.
    """
    env_info = {
        "environment_id": env.id if env else None,
        "environment_name": env.name if env else None,
    }
    if not source.strip():
        return CodeModuleFunctionPreview(**env_info)

    try:
        ast.parse(source)
    except SyntaxError as exc:
        return CodeModuleFunctionPreview(
            syntax_error=f"line {exc.lineno}: {exc.msg}",
            **env_info,
        )

    imports = _extract_top_level_imports(source)
    missing = _missing_in_env(imports, env)

    discovered, skipped = discover_module_nodes(
        module_id, source, include_undecorated=include_undecorated
    )
    explicit_mode = any(node.decorated for node in discovered)
    return CodeModuleFunctionPreview(
        registered=[node.manifest.name for node in discovered],
        functions=[
            CodeModuleFunctionShape(
                name=node.manifest.name,
                inputs=[p.name for p in node.manifest.inputs],
                params=[p.name for p in node.manifest.params],
                outputs=[p.name for p in node.manifest.outputs],
                decorated=node.decorated,
                wires=node.wires,
            )
            for node in discovered
        ],
        skipped=[{"name": name, "reason": reason} for name, reason in skipped],
        explicit_mode=explicit_mode,
        imports=imports,
        missing_in_env=missing,
        **env_info,
    )


@router.get("/{module_id}/preview", response_model=CodeModuleFunctionPreview)
async def preview_code_module(
    module_id: str, session: AsyncSession = Depends(get_session)
):
    module = await _load(session, module_id)
    env = await _workflow_environment(session, module.workflow_id)
    return _preview_payload(
        module.id,
        module.contents,
        env,
        include_undecorated=module.include_undecorated,
    )


@router.post(
    "/{module_id}/starter-graph",
    dependencies=[Depends(require_permission("code_module:write"))],
)
async def starter_graph(
    module_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """Generate an AST-aware starter graph from the module's source.

    Walks ``<var> = <call>`` chains and infers edges from variable flow,
    pre-populating literal arguments as default params. The user accepts
    or discards it on the canvas — we don't save here.
    """
    module = await _load(session, module_id)
    if not module.contents.strip():
        raise HTTPException(400, "Module has no contents.")
    try:
        return build_starter_graph(
            module.id,
            module.contents,
            include_undecorated=module.include_undecorated,
        )
    except SyntaxError as exc:
        raise HTTPException(400, f"Syntax error: {exc}") from exc


@router.get("/manifests/workflow/{workflow_id}", response_model=list[NodeManifest])
async def workflow_custom_node_manifests(
    workflow_id: str, session: AsyncSession = Depends(get_session)
):
    """Manifests visible to one workflow — global + the workflow's env + the
    workflow itself. The palette merges these on top of the built-ins.
    """
    from sqlalchemy import or_  # local import keeps the top-level slim

    workflow = await session.get(Workflow, workflow_id)
    env_id = workflow.environment_id if workflow else None
    stmt = select(CodeModule).where(
        or_(
            CodeModule.scope == "global",
            CodeModule.workflow_id == workflow_id,
            (
                (CodeModule.scope == "environment")
                & (CodeModule.environment_id == env_id)
            )
            if env_id
            else CodeModule.id.is_(None),
        )
    )
    rows = (await session.scalars(stmt)).all()
    manifests: list[NodeManifest] = []
    for module in rows:
        if not module.contents.strip():
            continue
        try:
            discovered, _ = discover_module_function_manifests(
                module.id,
                module.contents,
                include_undecorated=module.include_undecorated,
            )
        except SyntaxError:
            continue
        manifests.extend(discovered)
    return manifests
