import logging

from redis import Redis
from rq import Queue, Retry

from .config import settings


logger = logging.getLogger(__name__)


def get_redis_connection() -> Redis:
    return Redis.from_url(settings.redis_url)


def get_queue() -> Queue:
    return Queue(settings.queue_name, connection=get_redis_connection())


def _build_retry_policy() -> Retry | None:
    max_retries = max(0, int(settings.rq_retry_max))
    if max_retries <= 0:
        return None
    intervals = list(settings.rq_retry_intervals)
    if not intervals:
        intervals = [30]
    return Retry(max=max_retries, interval=intervals)


def enqueue_job(job_id: str) -> str:
    if settings.sync_jobs:
        # Development shortcut: run in-process without Redis/worker.
        from .tasks import process_job

        logger.info("WEBAPP_SYNC_JOBS enabled; processing job inline: %s", job_id)
        process_job(job_id)
        return f"sync-{job_id}"

    queue = get_queue()
    retry_policy = _build_retry_policy()
    rq_job = queue.enqueue(
        "backend.webapp.tasks.process_job",
        job_id,
        job_timeout="4h",
        result_ttl=24 * 3600,
        failure_ttl=7 * 24 * 3600,
        retry=retry_policy,
    )
    return rq_job.id
