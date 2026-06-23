"""GitHub sync service: push workflows to GitHub, pull from GitHub.

All GitHub API calls use httpx with the token from the org's github_oauth2
credential. Jobs are DB-backed (GithubSyncJob) and processed by the
github_sync_dispatch_loop in app.services.github_sync_jobs.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Credential, GithubSyncConfig, GithubSyncJob, Workflow
from app.services.audit import log_audit
from app.services import org_keys
from app.tenancy import run_as_system

import noodle_nodes  # noqa: F401 — registers built-in nodes
from noodle.models import WorkflowGraph
from noodle.sdk import registry as node_registry
from noodle_exporter import slugify, workflow_to_module
from noodle_importer import import_module

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
PUSH_MAX_ATTEMPTS = 3
PULL_MAX_ATTEMPTS = 3
_BACKOFF = (1, 4, 16)  # seconds

SyncOrigin = Literal["ui", "mcp", "api", "publish"]

COMMIT_MESSAGE_TEMPLATES: dict[str, str] = {
    "ui": "draft: {name}",
    "mcp": "mcp: {name}",
    "api": "api: {name}",
    "publish": "publish: {name} v{version}",
}


# ---------------------------------------------------------------------------
# Enqueue helper — called from every draft_graph write path
# ---------------------------------------------------------------------------


async def enqueue_github_push(
    session: AsyncSession,
    workflow: Workflow,
    origin: SyncOrigin,
) -> None:
    """Insert a push job if this org has a GitHub sync config.

    Takes the same session as the workflow write so the job is committed
    atomically with the graph change. Caller owns the commit.
    """
    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == workflow.org_id)
    )
    if cfg is None:
        return

    job_type = "push_publish" if origin == "publish" else "push_draft"
    job = GithubSyncJob(
        org_id=workflow.org_id,
        workflow_id=workflow.id,
        job_type=job_type,
        origin=origin,
        status="pending",
    )
    session.add(job)
    if workflow.github_sync_status not in ("conflict",):
        workflow.github_sync_status = "pending"


# ---------------------------------------------------------------------------
# Workflow → file path
# ---------------------------------------------------------------------------


def workflow_file_path(base_path: str, workflow: Workflow) -> str:
    slug = slugify(workflow.name)
    return base_path.rstrip("/") + "/" + slug + ".py"


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------


async def _get_token(session: AsyncSession, cfg: GithubSyncConfig) -> str | None:
    if not cfg.credential_id:
        return None
    cred = await session.get(Credential, cfg.credential_id)
    if cred is None:
        return None
    data = await org_keys.decrypt_credential_for(cred, session)
    return data.get("token") or data.get("access_token")


async def _github_get_file(
    client: httpx.AsyncClient, token: str, repo: str, path: str, ref: str
) -> dict | None:
    resp = await client.get(
        f"{GITHUB_API}/repos/{repo}/contents/{path}",
        params={"ref": ref},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def _github_get_branch(
    client: httpx.AsyncClient, token: str, repo: str, branch: str
) -> dict | None:
    resp = await client.get(
        f"{GITHUB_API}/repos/{repo}/branches/{branch}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def _github_create_branch(
    client: httpx.AsyncClient, token: str, repo: str, branch: str, from_sha: str
) -> None:
    resp = await client.post(
        f"{GITHUB_API}/repos/{repo}/git/refs",
        json={"ref": f"refs/heads/{branch}", "sha": from_sha},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    if resp.status_code == 422 and "Reference already exists" in resp.text:
        return  # concurrent creation — benign
    resp.raise_for_status()


async def _github_put_file(
    client: httpx.AsyncClient,
    token: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str,
    sha: str | None,
) -> dict:
    body: dict = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    resp = await client.put(
        f"{GITHUB_API}/repos/{repo}/contents/{path}",
        json=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Job processors
# ---------------------------------------------------------------------------


async def process_push_job(job_id: str) -> None:
    """Execute a push_draft or push_publish job. Loads its own DB session."""
    with run_as_system():
        async with SessionLocal() as session:
            job = await session.get(GithubSyncJob, job_id)
            if job is None or job.status not in ("pending", "failed"):
                return

            job.status = "processing"
            job.attempts += 1
            await session.commit()

            workflow = await session.get(Workflow, job.workflow_id)
            cfg = await session.scalar(
                select(GithubSyncConfig).where(GithubSyncConfig.org_id == job.org_id)
            )

            if workflow is None or cfg is None:
                job.status = "failed"
                job.error_message = "Workflow or sync config not found"
                await session.commit()
                return

            token = await _get_token(session, cfg)
            if not token:
                job.status = "failed"
                job.error_message = "No GitHub credential token found"
                await session.commit()
                return

            is_publish = job.job_type == "push_publish"
            target_branch = (
                cfg.main_branch if is_publish else f"draft/{slugify(workflow.name)}"
            )
            file_path = workflow_file_path(cfg.base_path, workflow)

            graph = workflow.draft_graph or {"nodes": [], "edges": []}
            try:
                content = workflow_to_module(
                    WorkflowGraph.model_validate(graph),
                    workflow.name,
                    registry=node_registry,
                )
            except Exception as exc:
                job.status = "failed"
                job.error_message = f"Export failed: {exc}"
                await session.commit()
                return

            version = workflow.published_version if is_publish else None
            msg_tpl = COMMIT_MESSAGE_TEMPLATES.get(job.origin, "update: {name}")
            message = msg_tpl.format(name=workflow.name, version=version)

            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    # Ensure draft branch exists (draft branches only)
                    if not is_publish:
                        branch_info = await _github_get_branch(
                            client, token, cfg.repo, target_branch
                        )
                        if branch_info is None:
                            main_info = await _github_get_branch(
                                client, token, cfg.repo, cfg.main_branch
                            )
                            if main_info is None:
                                raise RuntimeError(
                                    f"Main branch {cfg.main_branch!r} not found"
                                )
                            from_sha = main_info["commit"]["sha"]
                            await _github_create_branch(
                                client, token, cfg.repo, target_branch, from_sha
                            )

                    result = await _github_put_file(
                        client,
                        token,
                        cfg.repo,
                        file_path,
                        content,
                        message,
                        target_branch,
                        sha=workflow.github_sync_sha,
                    )

                new_sha = result["content"]["sha"]
                workflow.github_sync_sha = new_sha
                workflow.github_sync_status = "synced"
                job.status = "done"
                await log_audit(session, "github_push", "workflow", workflow.id, job.origin)
                await session.commit()

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code in (409, 422):
                    # Conflict: GitHub rejected because file SHA diverged
                    workflow.github_sync_status = "conflict"
                    job.status = "failed"
                    job.error_message = f"GitHub conflict (HTTP {status_code})"
                    await session.commit()
                elif status_code in (401, 403, 404) or job.attempts >= PUSH_MAX_ATTEMPTS:
                    workflow.github_sync_status = "error"
                    job.status = "failed"
                    job.error_message = str(exc)
                    await session.commit()
                else:
                    # Transient — schedule retry with backoff
                    delay = _BACKOFF[min(job.attempts - 1, len(_BACKOFF) - 1)]
                    job.status = "failed"
                    job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                    job.error_message = str(exc)
                    await session.commit()
            except Exception as exc:
                logger.exception("github push job %s failed: %s", job_id, exc)
                if job.attempts >= PUSH_MAX_ATTEMPTS:
                    workflow.github_sync_status = "error"
                    job.status = "failed"
                else:
                    delay = _BACKOFF[min(job.attempts - 1, len(_BACKOFF) - 1)]
                    job.status = "failed"
                    job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                job.error_message = str(exc)
                await session.commit()


async def process_pull_job(job_id: str) -> None:
    """Execute a github_pull job. Loads its own DB session."""
    with run_as_system():
        async with SessionLocal() as session:
            job = await session.get(GithubSyncJob, job_id)
            if job is None or job.status not in ("pending", "failed"):
                return

            job.status = "processing"
            job.attempts += 1
            await session.commit()

            workflow = await session.get(Workflow, job.workflow_id)
            cfg = await session.scalar(
                select(GithubSyncConfig).where(GithubSyncConfig.org_id == job.org_id)
            )
            if workflow is None or cfg is None:
                job.status = "failed"
                job.error_message = "Workflow or config not found"
                await session.commit()
                return

            token = await _get_token(session, cfg)
            if not token:
                job.status = "failed"
                job.error_message = "No GitHub credential token"
                await session.commit()
                return

            file_path = workflow_file_path(cfg.base_path, workflow)
            is_manual = job.file_sha is None

            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    file_info = await _github_get_file(
                        client, token, cfg.repo, file_path, cfg.main_branch
                    )
                if file_info is None:
                    job.status = "failed"
                    job.error_message = (
                        f"File {file_path} not found on {cfg.main_branch}"
                    )
                    await session.commit()
                    return

                incoming_sha = file_info["sha"]
                content_b64 = file_info["content"]
                source = base64.b64decode(content_b64).decode()

                # Conflict detection (skip for manual pull — user explicitly requested)
                if not is_manual and workflow.github_sync_sha:
                    sha_changed = incoming_sha != workflow.github_sync_sha
                    has_local_edits = workflow.draft_graph is not None
                    if sha_changed and has_local_edits:
                        workflow.github_sync_status = "conflict"
                        workflow.github_sync_conflict_sha = incoming_sha
                        job.status = "done"
                        await session.commit()
                        return

                # Parse and apply
                try:
                    new_graph = import_module(source)
                except (ImportError, ValueError, SyntaxError) as exc:
                    workflow.github_sync_status = "error"
                    job.status = "failed"
                    job.error_message = f"Import failed: {exc}"
                    await session.commit()
                    return

                workflow.draft_graph = new_graph.model_dump()
                workflow.github_sync_sha = incoming_sha
                workflow.github_sync_status = "synced"
                workflow.github_sync_conflict_sha = None
                job.status = "done"
                await log_audit(session, "github_pull", "workflow", workflow.id)
                await session.commit()

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code in (401, 403, 404) or job.attempts >= PULL_MAX_ATTEMPTS:
                    workflow.github_sync_status = "error"
                    job.status = "failed"
                else:
                    delay = _BACKOFF[min(job.attempts - 1, len(_BACKOFF) - 1)]
                    job.status = "failed"
                    job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                job.error_message = str(exc)
                await session.commit()
            except Exception as exc:
                logger.exception("github pull job %s failed: %s", job_id, exc)
                job.status = "failed"
                job.error_message = str(exc)
                await session.commit()


# ---------------------------------------------------------------------------
# Repo validation and creation
# ---------------------------------------------------------------------------


async def validate_repo_access(session: AsyncSession, cfg: GithubSyncConfig) -> dict:
    """Check whether the configured repo is reachable with the stored credential."""
    token = await _get_token(session, cfg)
    if not token:
        return {"accessible": False, "error": "No GitHub credential configured"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{cfg.repo}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "accessible": True,
                "private": data.get("private"),
                "default_branch": data.get("default_branch"),
            }
        if resp.status_code == 404:
            return {"accessible": False, "error": "Repository not found — create it first or check the name"}
        if resp.status_code in (401, 403):
            return {"accessible": False, "error": "Credential does not have access to this repository"}
        return {"accessible": False, "error": f"GitHub returned HTTP {resp.status_code}"}
    except httpx.TimeoutException:
        return {"accessible": False, "error": "Timed out connecting to GitHub"}


async def create_github_repo(
    session: AsyncSession,
    cfg: GithubSyncConfig,
    private: bool = True,
    description: str = "",
) -> dict:
    """Create the configured repo on GitHub using the stored credential."""
    token = await _get_token(session, cfg)
    if not token:
        raise ValueError("No GitHub credential configured")
    parts = cfg.repo.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: {cfg.repo!r} — expected owner/name")
    owner, name = parts

    async with httpx.AsyncClient(timeout=15) as client:
        org_resp = await client.get(
            f"{GITHUB_API}/orgs/{owner}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        create_url = (
            f"{GITHUB_API}/orgs/{owner}/repos"
            if org_resp.status_code == 200
            else f"{GITHUB_API}/user/repos"
        )
        resp = await client.post(
            create_url,
            json={
                "name": name,
                "private": private,
                "auto_init": True,
                "description": description or f"Noodle workflows — {owner}",
            },
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        if resp.status_code == 422:
            data = resp.json()
            msg = data.get("message", "Repository creation failed")
            raise ValueError(msg)
        resp.raise_for_status()
        data = resp.json()
        return {
            "created": True,
            "url": data.get("html_url", ""),
            "default_branch": data.get("default_branch", "main"),
        }


# ---------------------------------------------------------------------------
# HMAC verification
# ---------------------------------------------------------------------------


def verify_github_hmac(secret: str, payload: bytes, signature_header: str) -> bool:
    """Return True if the X-Hub-Signature-256 header matches the payload."""
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
