from celery import Celery

from worker.config import settings

celery_app = Celery(
    "noodle",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    timezone="UTC",
    enable_utc=True,
)

# Beat schedule. The single ``scheduler_tick`` task replaces the API's
# in-process loop when ``settings.enable_inprocess_scheduler=false`` —
# important when you run multiple API replicas, because only one Beat
# should own scheduling.
celery_app.conf.beat_schedule = {
    "noodle-scheduler-tick": {
        "task": "noodle.scheduler_tick",
        "schedule": float(max(15, settings.scheduler_tick_seconds)),
    },
}

celery_app.autodiscover_tasks(["worker"])
