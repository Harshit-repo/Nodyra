import ast
import inspect
import textwrap

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

import noodle_nodes  # noqa: F401 - importing registers the built-in nodes
from app.db import get_session
from app.models import CodeModule, Credential
from app.routers.credentials import _scope_rank
from app.security import require_permission
from app.services.org_keys import decrypt_credential_for
from noodle.models import NodeManifest
from noodle.sdk import registry
from noodle_nodes.integrations_v2.dynamic_options import call_loader, list_loader_ids

router = APIRouter(prefix="/nodes", tags=["nodes"])


@router.get("", response_model=list[NodeManifest])
async def list_nodes(
    category: str | None = Query(default=None),
) -> list[NodeManifest]:
    """Return every registered node manifest — the source for the editor palette."""
    manifests = registry.manifests()
    if category:
        manifests = [m for m in manifests if m.category == category]
    return manifests


@router.get("/{node_type}", response_model=NodeManifest)
async def get_node(node_type: str) -> NodeManifest:
    try:
        for manifest in registry.manifests():
            if manifest.id == node_type:
                return manifest
    except Exception as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Registry error: {exc}",
        ) from exc
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown node type '{node_type}'")


def _strip_decorators(func_source: str) -> str:
    """Return a function's source with any decorator lines removed.

    Code modules register *plain* top-level functions (no ``@node`` decorator),
    so a built-in's source must have its decorator stripped before it can be
    dropped into a custom module and re-registered.
    """
    src = textwrap.dedent(func_source)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not stmt.decorator_list:
                return src
            # On Python 3.8+ FunctionDef.lineno is the ``def`` line even when
            # decorated, so slice from there to drop the decorator(s).
            lines = src.splitlines()
            return "\n".join(lines[stmt.lineno - 1:]).rstrip() + "\n"
    return src


def _extract_function_source(module_source: str, func_name: str) -> str | None:
    """Pull a single top-level function's source out of a module's text."""
    try:
        tree = ast.parse(module_source)
    except SyntaxError:
        return None
    for stmt in tree.body:
        if (
            isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            and stmt.name == func_name
        ):
            segment = ast.get_source_segment(module_source, stmt)
            if segment:
                return segment.rstrip() + "\n"
    return None


@router.get("/{node_type}/source")
async def get_node_source(
    node_type: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return a node's Python source so it can be inspected and forked.

    Built-in nodes are read-only (they live in the installed package); their
    source is returned for transparency plus a decorator-stripped ``fork_source``
    that can seed a new custom node. User nodes (``user:<module_id>:<func>``)
    come from code modules and are editable in place.
    """
    if node_type.startswith("user:"):
        parts = node_type.split(":", 2)
        if len(parts) != 3:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown node type")
        _, module_id, func_name = parts
        module = await session.get(CodeModule, module_id)
        if module is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Code module not found")
        source = _extract_function_source(module.contents, func_name)
        if source is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Function '{func_name}' not found in module",
            )
        return {
            "node_type": node_type,
            "name": func_name,
            "kind": "user",
            "editable": True,
            "module_id": module_id,
            "func_name": func_name,
            "source": source,
            "fork_source": source,
        }

    try:
        node_def = registry.get(node_type)
    except Exception:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown node type '{node_type}'"
        ) from None
    generated_source = getattr(node_def.func, "__noodle_source__", None)
    if isinstance(generated_source, str) and generated_source.strip():
        raw = textwrap.dedent(generated_source)
    else:
        try:
            raw = inspect.getsource(node_def.func)
        except (OSError, TypeError) as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Source unavailable for '{node_type}': {exc}",
            ) from exc
    return {
        "node_type": node_type,
        "name": node_def.manifest.name,
        "kind": "builtin",
        "editable": False,
        "module_id": None,
        "func_name": node_def.func.__name__,
        "source": raw,
        "fork_source": _strip_decorators(raw),
    }


@router.get(
    "/dynamic-options/{loader_id}",
    dependencies=[Depends(require_permission("credential:read"))],
)
async def get_dynamic_options(
    loader_id: str,
    request: Request,
    credential_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return dynamic option choices for a node parameter dropdown.

    ``loader_id`` matches a registered :func:`register_loader` entry. All query
    params are forwarded to the loader as keyword arguments. When
    ``credential_id`` is supplied the credential is loaded, scope-checked, and
    decrypted server-side, and the plaintext dict is passed to the loader as
    ``credentials`` — secrets never travel in the query string.
    """
    import asyncio

    if loader_id not in list_loader_ids():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Unknown dynamic option loader '{loader_id}'",
        )

    kwargs: dict = {
        key: value
        for key, value in request.query_params.items()
        if key != "credential_id"
    }

    if credential_id:
        cred = await session.get(Credential, credential_id)
        if cred is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
        if (
            _scope_rank(
                cred,
                kwargs.get("workflow_id"),
                kwargs.get("environment_id"),
                kwargs.get("runner_pool_id"),
            )
            < 0
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Credential is not visible for the supplied scope.",
            )
        kwargs["credentials"] = await decrypt_credential_for(cred, session)

    try:
        options = await asyncio.to_thread(call_loader, loader_id, **kwargs)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Dynamic options loader '{loader_id}' failed: {exc}",
        ) from exc
    return {"loader_id": loader_id, "options": options}

