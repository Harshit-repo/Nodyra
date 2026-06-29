"""Community Node Registry router (MS4 Slice 4E).

Provides search, browse, and async-install of community node packages
published on PyPI and indexed in a GitHub-backed registry JSON file.
"""

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, get_session
from app.models import Environment, User
from app.security import optional_current_user, require_permission
from app.services.backends import build_environment

router = APIRouter(prefix="/node-registry", tags=["node-registry"])

# ── In-memory install-tracking store ─────────────────────────────────────
# MVP: dict-based. In production these could be Redis-backed; for the MVP the
# TTL is long enough that a restart is fine (in-flight installs will be
# orphaned and the frontend's poll loop will observe success/failure via the
# environment status instead).
_INSTALLS: dict[str, dict[str, Any]] = {}
_INSTALL_TTL_SECONDS = 3600  # 1 hour


async def _fetch_registry_index() -> dict[str, Any]:
    """Fetch and return the community node registry index."""
    url = settings.registry_index_url
    if not url:
        return {"packages": []}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def _match_package(pkg: dict[str, Any], query: str) -> bool:
    """Return True if *query* matches the package's name, description, or nodes."""
    q = query.lower()
    if q in pkg.get("name", "").lower():
        return True
    if q in pkg.get("description", "").lower():
        return True
    if q in pkg.get("id", "").lower():
        return True
    for node in pkg.get("nodes", []):
        if q in node.lower():
            return True
    return False


# ── Schemas (inline — lean MVP) ──────────────────────────────────────────
# Full pydantic schemas would be over-engineered for 4 endpoints; plain dicts
# returned from FastAPI are auto-converted to JSON responses.


@router.get("")
@router.get("/search")
async def search_registry(
    q: str = Query("", description="Search query"),
    category: str = Query("", description="Category filter (unused in MVP)"),
    _user: User | None = Depends(optional_current_user),
) -> dict[str, Any]:
    """Search the community node registry.

    Returns matching packages from the registry index filtered by query.
    When *q* is empty, returns all packages.
    """
    if not settings.allow_registry:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Community Node Registry is disabled on this server "
            "(NOODLE_ALLOW_REGISTRY=false).",
        )

    try:
        index = await _fetch_registry_index()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Registry index unreachable (HTTP {exc.response.status_code}).",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Registry index unreachable: {exc}.",
        ) from exc

    packages: list[dict[str, Any]] = index.get("packages", [])
    if q:
        packages = [p for p in packages if _match_package(p, q)]

    return {"packages": packages}


@router.get("/packages/{package_id:path}")
async def get_package(
    package_id: str,
    _user: User | None = Depends(optional_current_user),
) -> dict[str, Any]:
    """Return a single package's details from the registry."""
    if not settings.allow_registry:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Community Node Registry is disabled on this server.",
        )

    try:
        index = await _fetch_registry_index()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Registry index unreachable (HTTP {exc.response.status_code}).",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Registry index unreachable: {exc}.",
        ) from exc

    for pkg in index.get("packages", []):
        if pkg.get("id") == package_id:
            return pkg

    raise HTTPException(
        status.HTTP_404_NOT_FOUND,
        f"Package {package_id!r} not found in registry.",
    )


@router.post(
    "/install",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("node_registry:install"))],
)
async def install_package(
    body: dict[str, Any],
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Enqueue a community node package for installation.

    The install is async: the package's ``pypi_package`` is added to the
    environment's ``packages`` array immediately, then a background task
    rebuilds the environment venv.  The frontend polls ``GET /installs/{id}``
    to track progress.
    """
    if not settings.allow_registry:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Community Node Registry is disabled on this server.",
        )

    package_id: str | None = body.get("package_id")
    environment_id: str | None = body.get("environment_id")

    if not package_id or not environment_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Both 'package_id' and 'environment_id' are required.",
        )

    # 1. Validate the package exists in the registry index
    try:
        index = await _fetch_registry_index()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Registry index unreachable (HTTP {exc.response.status_code}).",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Registry index unreachable: {exc}.",
        ) from exc

    pkg_meta: dict[str, Any] | None = None
    for pkg in index.get("packages", []):
        if pkg.get("id") == package_id:
            pkg_meta = pkg
            break

    if pkg_meta is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Package {package_id!r} not found in registry.",
        )

    pypi_package = pkg_meta.get("pypi_package") or pkg_meta.get("id", package_id)

    # 2. Verify the environment exists
    env = await session.get(Environment, environment_id)
    if env is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Environment not found."
        )

    # 3. Add the PyPI package to the environment's packages list
    packages = list(env.packages)
    if pypi_package not in packages:
        packages.append(pypi_package)
        env.packages = packages
        env.status = "pending"
        await session.commit()
        await session.refresh(env)

    # 4. Create install tracking record
    install_id = uuid.uuid4().hex[:16]
    _INSTALLS[install_id] = {
        "status": "pending",
        "error": None,
        "environment_id": environment_id,
        "package_id": package_id,
        "pypi_package": pypi_package,
    }

    # 5. Kick off async build
    background.add_task(_install_task, install_id, environment_id)

    return {"install_id": install_id, "status": "pending"}


async def _install_task(install_id: str, environment_id: str) -> None:
    """Background task that tracks install progress."""
    record = _INSTALLS.get(install_id)
    if record is None:
        return
    try:
        record["status"] = "installing"
        await build_environment(environment_id)
        record["status"] = "ready"
    except BaseException as exc:  # noqa: BLE001
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        # Best-effort rollback: remove the package from the environment
        await _rollback_install(environment_id, record)


async def _rollback_install(environment_id: str, record: dict[str, Any]) -> None:
    """Remove the pypi_package from the environment on install failure."""
    try:
        async with SessionLocal() as rollback_session:
            env = await rollback_session.get(Environment, environment_id)
            if env is not None:
                pkg_name = record.get("pypi_package", "")
                pkgs = list(env.packages)
                if pkg_name in pkgs:
                    pkgs.remove(pkg_name)
                    env.packages = pkgs
                    env.status = "ready"
                    await rollback_session.commit()
    except BaseException:
        pass  # best-effort rollback


@router.get("/installs/{install_id}")
async def get_install_status(
    install_id: str,
    _user: User | None = Depends(optional_current_user),
) -> dict[str, Any]:
    """Poll the status of an in-flight package install."""
    record = _INSTALLS.get(install_id)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Install record not found or expired."
        )
    return {
        "install_id": install_id,
        "status": record["status"],
        "error": record.get("error"),
        "environment_id": record.get("environment_id"),
        "package_id": record.get("package_id"),
    }
