"""
Celery application.
All long-running generation tasks run here so FastAPI requests return immediately.

Worker startup:
  celery -A tasks.celery_app worker --loglevel=info --concurrency=4
"""
from celery import Celery
from config import settings

celery_app = Celery(
    "course_gen",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,          # expose STARTED state to frontend
    result_expires=60 * 60 * 24,      # keep results 24h
    worker_prefetch_multiplier=1,     # one task at a time per worker slot
    task_acks_late=True,              # re-queue on worker crash
)

# Auto-discover tasks in tasks/ package
celery_app.autodiscover_tasks(["tasks"])
