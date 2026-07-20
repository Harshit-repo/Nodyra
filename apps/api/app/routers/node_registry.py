"""Community Node Registry router (MS4 Slice 4E).

Provides search, browse, and async-install of community node packages
published on PyPI and indexed in a GitHub-backed registry JSON file.
"""

import json
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Environment, EnvironmentBuildJob, User
from app.security import optional_current_user, require_permission
from app.services.environment_builds import (
    enqueue_environment_build,
    notify_environment_build_workers,
)
from app.services.metrics import registry_install_total, registry_search_total
from app.services.registry_trust import assess_registry_package, package_with_trust
from nodyra_nodes.http_security import assert_public_http_url

router = APIRouter(prefix="/node-registry", tags=["node-registry"])

_MAX_REGISTRY_BYTES = 5 * 1024 * 1024


async def _fetch_registry_index() -> dict[str, Any]:
    """Fetch and return the community node registry index."""
    url = settings.registry_index_url
    if not url:
        return {"packages": []}
    assert_public_http_url(url, context="registry index URL")
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        async with client.stream("GET", url, headers={"Accept": "application/json"}) as resp:
            resp.raise_for_status()
            payload = bytearray()
            async for chunk in resp.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > _MAX_REGISTRY_BYTES:
                    raise ValueError("Registry index exceeds the 5 MiB safety limit")
    document = json.loads(payload)
    if not isinstance(document, dict) or not isinstance(document.get("packages", []), list):
        raise ValueError("Registry index has an invalid shape")
    return document


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


def _package_versions(package: dict[str, Any]) -> list[dict[str, Any]]:
    base = {key: value for key, value in package.items() if key != "versions"}
    versions = package.get("versions")
    if not isinstance(versions, list) or not versions:
        return [base]
    merged = [
        {**base, **version}
        for version in versions
        if isinstance(version, dict)
    ]
    if not any(item.get("version") == base.get("version") for item in merged):
        merged.append(base)
    return merged


def _present_package(package: dict[str, Any]) -> dict[str, Any]:
    base = {key: value for key, value in package.items() if key != "versions"}
    return {
        **package_with_trust(base),
        "versions": [package_with_trust(version) for version in _package_versions(package)],
    }


def _select_package_version(package: dict[str, Any], version: str | None) -> dict[str, Any] | None:
    versions = _package_versions(package)
    if not version:
        current = package.get("version")
        return next((item for item in versions if item.get("version") == current), versions[0])
    return next((item for item in versions if item.get("version") == version), None)


# Registry responses retain the signed index fields and add server-computed
# trust metadata. Trust status supplied by the remote index is never accepted.


@router.get("")
@router.get("/search")
async def search_registry(
    q: str = Query("", description="Search query"),
    category: str = Query("", max_length=80, description="Category filter"),
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
            "(NODYRA_ALLOW_REGISTRY=false).",
        )

    try:
        index = await _fetch_registry_index()
    except httpx.HTTPStatusError as exc:
        registry_search_total.inc(outcome="upstream_http_error")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Registry index unreachable (HTTP {exc.response.status_code}).",
        ) from exc
    except httpx.RequestError as exc:
        registry_search_total.inc(outcome="upstream_network_error")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Registry index unreachable: {exc}.",
        ) from exc
    except ValueError as exc:
        registry_search_total.inc(outcome="invalid_index")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Registry index was rejected: {exc}.",
        ) from exc
    registry_search_total.inc(outcome="success")

    packages: list[dict[str, Any]] = [
        _present_package(package)
        for package in index.get("packages", [])
        if isinstance(package, dict)
    ]
    if q:
        packages = [p for p in packages if _match_package(p, q)]
    if category:
        expected = category.strip().lower()
        packages = [
            package
            for package in packages
            if expected in {
                str(value).lower()
                for value in package.get("categories", [])
            }
            or str(package.get("category") or "").lower() == expected
        ]

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
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Registry index was rejected: {exc}.",
        ) from exc

    for pkg in index.get("packages", []):
        if pkg.get("id") == package_id:
            return _present_package(pkg)

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
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(optional_current_user),
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
    requested_version: str | None = body.get("version")

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
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Registry index was rejected: {exc}.",
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

    selected_meta = _select_package_version(pkg_meta, requested_version)
    if selected_meta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Package version not found.")
    trust = assess_registry_package(selected_meta)
    if not trust.installable or not trust.locked_spec:
        registry_install_total.inc(status="blocked")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "registry_package_not_trusted",
                "status": trust.status,
                "reason": trust.reason,
            },
        )
    locked_spec = trust.locked_spec
    try:
        assert_public_http_url(str(selected_meta.get("distribution_url")), context="registry distribution URL")
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "registry_distribution_blocked", "reason": str(exc)},
        ) from exc

    # 2. Verify the environment exists
    env = await session.get(Environment, environment_id)
    if env is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Environment not found."
        )
    if env.backend != "venv":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Signed registry distributions currently require a venv environment backend.",
        )

    # 3. Add the PyPI package to the environment's packages list
    packages = list(env.packages)
    package_name = str(selected_meta.get("pypi_package"))
    previous_specs = [
        spec for spec in packages if spec == package_name or spec.startswith(f"{package_name} @ ")
    ]
    packages = [spec for spec in packages if spec not in previous_specs]
    if locked_spec not in packages:
        packages.append(locked_spec)
        env.packages = packages
        env.status = "pending"
    job = await enqueue_environment_build(
        session,
        env,
        reason=f"registry:{package_id}"[:40],
        requested_by=user,
    )
    await session.commit()
    await session.refresh(job)
    await notify_environment_build_workers()
    registry_install_total.inc(status="accepted")
    return {"install_id": job.id, "status": job.status}


@router.get("/installs/{install_id}")
async def get_install_status(
    install_id: str,
    session: AsyncSession = Depends(get_session),
    _user: User | None = Depends(optional_current_user),
) -> dict[str, Any]:
    """Poll the durable environment-build job used for this install."""
    record = await session.get(EnvironmentBuildJob, install_id)
    if record is None or await session.get(Environment, record.environment_id) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Install record not found or expired."
        )
    return {
        "install_id": install_id,
        "status": record.status,
        "error": record.last_error,
        "environment_id": record.environment_id,
        "package_id": record.reason.removeprefix("registry:"),
        "attempts": record.attempts,
        "max_attempts": record.max_attempts,
    }
