import asyncio
import logging
from datetime import UTC, datetime

from prometheus_client import start_http_server
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal, engine
from app.engine import (
    mark_stale_workers,
    reap_expired_leases,
    release_due_tasks,
    republish_orphaned_queued,
)
from app.metrics import (
    DEAD_LETTER_COUNT,
    OUTBOX_PUBLISHED,
    OUTBOX_PUBLISH_FAILURES,
    QUEUE_DEPTH,
    SCHEDULER_FAILURES,
)
from app.models import OutboxEvent, Task, TaskState
from app.queue import RedisTransport
from app.telemetry import configure_telemetry, tracer

logger = logging.getLogger(__name__)


class OutboxPublisher:
    def __init__(self, transport: RedisTransport) -> None:
        self.transport = transport

    async def publish_batch(self, limit: int = 100) -> int:
        published = 0
        async with SessionLocal() as session:
            events = list(
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for event in events:
                event.publish_attempts += 1
                try:
                    with tracer().start_as_current_span(
                        "outbox.publish", attributes={"outbox.event_type": event.event_type}
                    ):
                        if event.event_type == "task.ready":
                            await self.transport.publish_task(
                                queue=str(event.payload["queue"]),
                                task_id=str(event.payload["task_id"]),
                                workflow_id=str(event.payload["workflow_id"]),
                            )
                        elif event.event_type == "task.dead_lettered":
                            await self.transport.publish_dead_letter(event.payload)
                        else:
                            await self.transport.redis.xadd(
                                "dwe:events",
                                {"type": event.event_type, "payload": repr(event.payload)},
                            )
                    event.published_at = datetime.now(UTC)
                    OUTBOX_PUBLISHED.labels(event_type=event.event_type).inc()
                    published += 1
                except Exception:
                    OUTBOX_PUBLISH_FAILURES.labels(event_type=event.event_type).inc()
                    logger.exception(
                        "outbox publish failed",
                        extra={
                            "outbox_event_id": str(event.id),
                            "outbox_event_type": event.event_type,
                        },
                    )
                    # Keep the row unpublished. A later pass retries it. If Redis accepted
                    # the event but the DB commit is lost, this intentionally republishes.
                    continue
            await session.commit()
        return published


async def update_metrics(redis: Redis) -> None:
    settings = get_settings()
    transport = RedisTransport(redis)
    for queue in settings.queues:
        for partition in range(settings.queue_partitions):
            stream = transport.stream_name(queue, partition)
            depth = 0
            try:
                groups = await redis.xinfo_groups(stream)
                for group in groups:
                    if group.get("name") == "dwe-workers":
                        depth = int(group.get("pending", 0)) + int(group.get("lag") or 0)
                        break
            except Exception:
                depth = 0
            QUEUE_DEPTH.labels(queue=queue, partition=str(partition)).set(depth)

    async with SessionLocal() as session:
        dead = await session.scalar(
            select(func.count()).select_from(Task).where(Task.state == TaskState.DEAD_LETTER.value)
        )
    DEAD_LETTER_COUNT.set(int(dead or 0))


async def run_scheduler() -> None:
    settings = get_settings()
    configure_telemetry(engine=engine)
    start_http_server(settings.metrics_port)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    transport = RedisTransport(redis)
    await transport.ensure_groups(settings.queues)
    publisher = OutboxPublisher(transport)
    try:
        while True:
            try:
                await publisher.publish_batch()
                async with SessionLocal() as session:
                    await release_due_tasks(session)
                async with SessionLocal() as session:
                    await reap_expired_leases(session)
                async with SessionLocal() as session:
                    await republish_orphaned_queued(
                        session, older_than_seconds=max(5, settings.lease_seconds)
                    )
                async with SessionLocal() as session:
                    await mark_stale_workers(
                        session, stale_after_seconds=settings.heartbeat_seconds * 3
                    )
                await update_metrics(redis)
            except Exception:
                SCHEDULER_FAILURES.labels(operation="iteration").inc()
                logger.exception("scheduler iteration failed")
            await asyncio.sleep(settings.scheduler_poll_seconds)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(run_scheduler())
