"""GitHub sync settings CRUD, manual pull, and conflict resolve endpoints."""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import GithubSyncConfig, GithubSyncJob, Workflow
from app.schemas import (
    GithubConflictResolveRequest,
    GithubSyncConfigCreate,
    GithubSyncConfigInfo,
)
from app.security import require_permission
from app.services.audit import log_audit
from app.services.github_sync import enqueue_github_push
from app.services.github_sync_jobs import notify_sync_workers
from app.services.licensing import Feature, require_feature
from app.tenancy import active_org_id

router = APIRouter(tags=["github-sync"])
logger = logging.getLogger(__name__)


def _webhook_url(org_id: str) -> str:
    try:
        from app.config import settings as app_settings
        base = getattr(app_settings, "public_url", "") or ""
    except Exception:  # noqa: BLE001
        base = ""
    return f"{base.rstrip('/')}/webhooks/github-sync/{org_id}"


def _config_info(cfg: GithubSyncConfig, org_id: str) -> GithubSyncConfigInfo:
    return GithubSyncConfigInfo(
        id=cfg.id,
        org_id=cfg.org_id,
        repo=cfg.repo,
        base_path=cfg.base_path,
        main_branch=cfg.main_branch,
        credential_id=cfg.credential_id,
        webhook_url=_webhook_url(org_id),
    )


# ---------------------------------------------------------------------------
# Settings CRUD
# ---------------------------------------------------------------------------


@router.get("/github-sync/config", response_model=GithubSyncConfigInfo | None)
async def get_github_sync_config(
    _: None = Depends(require_permission("workflow:write")),
    session: AsyncSession = Depends(get_session),
) -> GithubSyncConfigInfo | None:
    org_id = active_org_id() or "default"
    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == org_id)
    )
    if cfg is None:
        return None
    return _config_info(cfg, org_id)


@router.put("/github-sync/config", response_model=GithubSyncConfigInfo)
async def upsert_github_sync_config(
    body: GithubSyncConfigCreate,
    _: None = Depends(require_permission("workflow:write")),
    __: None = Depends(require_feature(Feature.GIT_SYNC)),
    session: AsyncSession = Depends(get_session),
) -> GithubSyncConfigInfo:
    org_id = active_org_id() or "default"
    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == org_id)
    )
    if cfg is None:
        cfg = GithubSyncConfig(
            org_id=org_id,
            webhook_secret=secrets.token_hex(32),
        )
        session.add(cfg)
    cfg.repo = body.repo
    cfg.base_path = body.base_path
    cfg.main_branch = body.main_branch
    if body.credential_id is not None:
        cfg.credential_id = body.credential_id
    await log_audit(session, "github_sync_connect", "org", org_id, body.repo)
    await session.commit()
    await session.refresh(cfg)
    return _config_info(cfg, org_id)


@router.get("/github-sync/config/webhook-secret")
async def get_webhook_secret(
    _: None = Depends(require_permission("workflow:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    org_id = active_org_id() or "default"
    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == org_id)
    )
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No sync config")
    return {"webhook_secret": cfg.webhook_secret}


@router.delete("/github-sync/config", status_code=204)
async def delete_github_sync_config(
    _: None = Depends(require_permission("workflow:write")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    org_id = active_org_id() or "default"
    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == org_id)
    )
    if cfg is not None:
        await log_audit(session, "github_sync_disconnect", "org", org_id, cfg.repo)
        await session.delete(cfg)
        await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Manual pull + conflict resolution
# ---------------------------------------------------------------------------


@router.post("/workflows/{workflow_id}/github-pull", status_code=202)
async def manual_github_pull(
    workflow_id: str,
    _: None = Depends(require_permission("workflow:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    job = GithubSyncJob(
        org_id=workflow.org_id,
        workflow_id=workflow_id,
        job_type="pull",
        origin="manual",
        file_sha=None,
        status="pending",
    )
    session.add(job)
    await session.commit()
    notify_sync_workers()
    return {"status": "queued", "job_id": job.id}


@router.post("/workflows/{workflow_id}/github-conflict/resolve")
async def resolve_github_conflict(
    workflow_id: str,
    body: GithubConflictResolveRequest,
    _: None = Depends(require_permission("workflow:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    if workflow.github_sync_status != "conflict":
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow is not in conflict state")

    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == workflow.org_id)
    )
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No sync config")

    if body.side == "noodle":
        # Noodle wins: reset SHA so next push overwrites GitHub, enqueue atomically
        workflow.github_sync_sha = workflow.github_sync_conflict_sha
        workflow.github_sync_status = "pending"
        workflow.github_sync_conflict_sha = None
        await log_audit(session, "github_conflict_resolved", "workflow", workflow_id, "noodle")
        await enqueue_github_push(session, workflow, "ui")
        await session.commit()
        notify_sync_workers()
    else:  # github wins
        # Enqueue a pull to overwrite noodle with GitHub's version
        job = GithubSyncJob(
            org_id=workflow.org_id,
            workflow_id=workflow_id,
            job_type="pull",
            origin="conflict_resolve",
            file_sha=None,
            status="pending",
        )
        session.add(job)
        workflow.github_sync_status = "pending"
        workflow.github_sync_conflict_sha = None
        await log_audit(session, "github_conflict_resolved", "workflow", workflow_id, "github")
        await session.commit()
        notify_sync_workers()

    return {"status": "ok", "side": body.side}
