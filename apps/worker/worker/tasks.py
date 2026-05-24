import httpx

from worker.celery_app import celery_app
from worker.config import settings


@celery_app.task(name="noodle.ping")
def ping() -> str:
    """Smoke-test task."""
    return "pong"


@celery_app.task(name="noodle.scheduler_tick")
def scheduler_tick() -> dict:
    """Beat-driven scheduler tick.

    All this does is POST to the API's ``/internal/scheduler/tick`` so the
    API stays the single execution authority — schedule evaluation,
    start_run, DB writes, broker events, all happen in the API process.
    The worker is just the heartbeat (and in multi-replica deployments,
    the single owner of that heartbeat).
    """
    headers = {}
    if settings.internal_api_token:
        headers["X-Noodle-Internal-Token"] = settings.internal_api_token
    url = f"{settings.api_base_url.rstrip('/')}/internal/scheduler/tick"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers=headers)
        resp.raise_for_status()
        return resp.json()
