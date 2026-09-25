"""User code modules: upload-to-nodes.

Each row is a Python file whose top-level functions appear as nodes. API
preview and palette manifest generation use static AST discovery, so uploaded
code is not executed in the API process. Actual workflow runs register the
module source in the runner/runtime environment.
"""

import ast
import asyncio
import shutil
import subprocess
import sys

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
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
    GenerateNodeRequest,
    GenerateNodeResponse,
)
from app.security import audit_recorder, optional_current_user, require_permission
from app.services.ai_builder import generate_custom_node
from app.services.audit import AuditRecorder, log_audit
from app.services.starter_graph import build_starter_graph
from nodyra.models import NodeManifest
from nodyra.sdk import discover_module_function_manifests, discover_module_nodes

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
                ((CodeModule.scope == "environment") & (CodeModule.environment_id == env_id))
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
    if body.scope == "workflow" and await session.get(Workflow, body.workflow_id) is None:
        raise HTTPException(404, "Workflow not found")
    if body.scope == "environment" and await session.get(Environment, body.environment_id) is None:
        raise HTTPException(404, "Environment not found")

    module = CodeModule(
        scope=body.scope,
        workflow_id=body.workflow_id if body.scope == "workflow" else None,
        environment_id=body.environment_id if body.scope == "environment" else None,
        name=body.name,
        contents=body.contents or "",
        include_undecorated=body.include_undecorated,
        module_metadata=body.metadata or {},
    )
    session.add(module)
    await log_audit(
        session,
        "create",
        "code_module",
        detail=body.name,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await session.commit()
    await session.refresh(module)
    return module


@router.get("/{module_id}", response_model=CodeModuleInfo)
async def get_code_module(module_id: str, session: AsyncSession = Depends(get_session)):
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
    audit: AuditRecorder = Depends(audit_recorder),
):
    module = await _load(session, module_id)
    if body.name is not None:
        module.name = body.name
    if body.contents is not None:
        module.contents = body.contents
    if body.include_undecorated is not None:
        module.include_undecorated = body.include_undecorated
    await session.commit()
    await audit(
        "update", "code_module", module.id,
        "fields=" + ",".join(sorted(body.model_dump(exclude_unset=True))),
    )
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
    await log_audit(
        session,
        "delete",
        "code_module",
        module.id,
        module.name,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
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
async def preview_code_module(module_id: str, session: AsyncSession = Depends(get_session)):
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
async def starter_graph(module_id: str, session: AsyncSession = Depends(get_session)) -> dict:
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


class CodeFormatRequest(BaseModel):
    code: str = Field(max_length=1_000_000)


class CodeFormatResult(BaseModel):
    code: str
    changed: bool
    error: str | None = None


async def _ruff_format(source: str) -> tuple[str, str | None]:
    """Format Python with ``ruff format`` (stdin → stdout).

    Runs in a thread so the synchronous ``subprocess.run`` never blocks the
    event loop. Degrades gracefully: if ruff isn't installed or errors, the
    original source is returned with an ``error`` so callers can no-op rather
    than block a save.
    """
    loop = asyncio.get_running_loop()
    ruff_bin = shutil.which("ruff")
    cmd = [ruff_bin, "format", "-"] if ruff_bin else [sys.executable, "-m", "ruff", "format", "-"]

    def _run() -> tuple[str, str | None]:
        try:
            proc = subprocess.run(
                cmd,
                input=source,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return source, f"formatter unavailable: {exc}"
        if proc.returncode != 0:
            return source, (proc.stderr.strip() or "formatting failed")
        return proc.stdout, None

    return await loop.run_in_executor(None, _run)


@router.post(
    "/format",
    response_model=CodeFormatResult,
    dependencies=[Depends(require_permission("code_module:write"))],
)
async def format_code(body: CodeFormatRequest) -> CodeFormatResult:
    """Format a Python snippet with ruff. Stateless — formats text only and
    never executes the code. Returns the original source unchanged on any
    syntax/formatter error so 'format on save' can safely fall through.
    """
    source = body.code or ""
    if not source.strip():
        return CodeFormatResult(code=source, changed=False, error=None)
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return CodeFormatResult(code=source, changed=False, error=f"line {exc.lineno}: {exc.msg}")
    formatted, err = await _ruff_format(source)
    if err is not None:
        return CodeFormatResult(code=source, changed=False, error=err)
    return CodeFormatResult(code=formatted, changed=formatted != source, error=None)


class CodeLintRequest(BaseModel):
    code: str = Field(max_length=1_000_000)


class LintDiagnostic(BaseModel):
    line: int
    column: int
    code: str | None = None
    message: str
    severity: str = "warning"  # "error" | "warning"


class CodeLintResult(BaseModel):
    diagnostics: list[LintDiagnostic]
    # null when ruff produced diagnostics; set when only the syntax fallback ran
    # (e.g. ruff unavailable) so the UI can decide how loudly to surface gaps.
    linter: str  # "ruff" | "syntax" | "none"


async def _ruff_check(source: str) -> tuple[list[LintDiagnostic], str]:
    """Lint Python with ``ruff check`` (stdin → JSON).

    Runs in a thread so the synchronous ``subprocess.run`` never blocks the
    event loop. Returns ``(diagnostics, linter)``. Degrades to an ast-based
    syntax check when ruff is unavailable so the editor still flags hard
    errors. Never executes the code.
    """
    import json

    loop = asyncio.get_running_loop()
    ruff_bin = shutil.which("ruff")
    cmd = ([ruff_bin] if ruff_bin else [sys.executable, "-m", "ruff"]) + [
        "check",
        "--output-format=json",
        "--stdin-filename=code.py",
        "-",
    ]

    def _run() -> tuple[list[LintDiagnostic], str]:
        try:
            proc = subprocess.run(
                cmd,
                input=source,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return _syntax_only(source), "syntax"

        out = proc.stdout.strip()
        if not out:
            return ([], "ruff") if proc.returncode in (0, 1) else (_syntax_only(source), "syntax")
        try:
            raw = json.loads(out)
        except json.JSONDecodeError:
            return _syntax_only(source), "syntax"

        diags: list[LintDiagnostic] = []
        for item in raw:
            loc = item.get("location") or {}
            code = item.get("code")
            diags.append(
                LintDiagnostic(
                    line=int(loc.get("row", 1)),
                    column=int(loc.get("column", 1)),
                    code=code,
                    message=item.get("message", ""),
                    severity="error" if _is_error_code(code) else "warning",
                )
            )
        return diags, "ruff"

    return await loop.run_in_executor(None, _run)


def _is_error_code(code: str | None) -> bool:
    """Syntax/parse failures are surfaced as errors; lint findings as warnings.

    Ruff reports parse failures as ``invalid-syntax`` (newer) or ``E999``
    (older), and ``None`` for unkeyed parse panics — all are hard errors.
    """
    if not code:
        return True
    return code in ("E999", "invalid-syntax") or code.startswith("E9")


def _syntax_only(source: str) -> list[LintDiagnostic]:
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return [
            LintDiagnostic(
                line=exc.lineno or 1,
                column=(exc.offset or 1),
                code="E999",
                message=exc.msg or "syntax error",
                severity="error",
            )
        ]
    return []


@router.post(
    "/lint",
    response_model=CodeLintResult,
    dependencies=[Depends(require_permission("code_module:write"))],
)
async def lint_code(body: CodeLintRequest) -> CodeLintResult:
    """Lint a Python snippet with ruff (or an ast syntax check as fallback).

    Stateless and never executes the code. Empty input yields no diagnostics.
    """
    source = body.code or ""
    if not source.strip():
        return CodeLintResult(diagnostics=[], linter="none")
    diags, linter = await _ruff_check(source)
    return CodeLintResult(diagnostics=diags, linter=linter)


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
            ((CodeModule.scope == "environment") & (CodeModule.environment_id == env_id))
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


@router.post(
    "/generate-node",
    response_model=GenerateNodeResponse,
    dependencies=[Depends(require_permission("code_module:write"))],
)
async def generate_node_from_description(
    body: GenerateNodeRequest,
    session: AsyncSession = Depends(get_session),
) -> GenerateNodeResponse:
    """Generate a ``@node``-decorated Python function from a natural language description.

    Returns the generated code (or a TODO fallback) that the client displays
    in Monaco for review/editing before the user calls ``POST /code-modules``
    to save it permanently.
    """
    # Gather existing user: node IDs to detect collisions
    existing_stmt = select(CodeModule)
    existing_rows = (await session.scalars(existing_stmt)).all()
    existing_ids: list[str] = []
    for module in existing_rows:
        if module.contents.strip():
            try:
                discovered, _ = discover_module_function_manifests(
                    module.id,
                    module.contents,
                    include_undecorated=module.include_undecorated,
                )
                for manifest in discovered:
                    existing_ids.append(manifest.id)
            except SyntaxError:
                continue

    result = await generate_custom_node(
        body.description,
        session=session,
        org_id="default",
        existing_node_ids=existing_ids,
    )

    return GenerateNodeResponse(
        code=result.get("code", ""),
        node_id=result.get("node_id", "custom__generated"),
        node_name=result.get("node_name", "Generated Node"),
        input_ports=result.get("input_ports", {"main": "any"}),
        output_ports=result.get("output_ports", {"result": "any"}),
        is_template=result.get("is_template", False),
        warnings=result.get("warnings", []),
    )
