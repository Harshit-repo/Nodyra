from worker.celery_app import celery_app


@celery_app.task(name="noodle.ping")
def ping() -> str:
    """Smoke-test task. Workflow run tasks arrive with later milestones."""
    return "pong"
