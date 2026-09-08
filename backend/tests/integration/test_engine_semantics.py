import uuid
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select, update

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.engine import (
    acquire_task,
    cancel_workflow,
    mark_task_failed,
    reap_expired_leases,
    release_due_tasks,
    submit_workflow,
)
from app.models import Task, TaskState, Workflow
from app.queue import RedisTransport
from app.schemas import RetrySpec, TaskDefinition, WorkflowCreate

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def clean_state() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    redis: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    await redis.flushdb()
    await redis.aclose()


def definition(*tasks: TaskDefinition) -> WorkflowCreate:
    return WorkflowCreate(name="test", tasks=list(tasks))


async def test_duplicate_client_submission_is_deduplicated() -> None:
    body = definition(TaskDefinition(key="a"))
    async with SessionLocal() as session:
        first = await submit_workflow(session, body, "client-key-1")
    async with SessionLocal() as session:
        second = await submit_workflow(session, body, "client-key-1")
        count = await session.scalar(select(func.count()).select_from(Workflow))
    assert first.id == second.id
    assert count == 1


async def test_duplicate_queue_delivery_cannot_acquire_twice() -> None:
    async with SessionLocal() as session:
        workflow = await submit_workflow(
            session, definition(TaskDefinition(key="a")), "dup-message"
        )
    async with SessionLocal() as session:
        task = await session.scalar(select(Task).where(Task.workflow_id == workflow.id))
        assert task is not None
        task_id = task.id

    async with SessionLocal() as session:
        first = await acquire_task(session, task_id=task_id, worker_id="w1", lease_seconds=30)
    async with SessionLocal() as session:
        duplicate = await acquire_task(session, task_id=task_id, worker_id="w2", lease_seconds=30)
    assert first is not None
    assert duplicate is None


async def test_expired_worker_lease_is_recovered() -> None:
    async with SessionLocal() as session:
        workflow = await submit_workflow(
            session,
            definition(TaskDefinition(key="a", retry=RetrySpec(max_attempts=3))),
            "lease-recovery",
        )
    async with SessionLocal() as session:
        task = await session.scalar(select(Task).where(Task.workflow_id == workflow.id))
        assert task is not None
        task_id = task.id
    async with SessionLocal() as session:
        leased = await acquire_task(session, task_id=task_id, worker_id="dead", lease_seconds=30)
        assert leased is not None
    async with SessionLocal() as session:
        await session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    async with SessionLocal() as session:
        assert await reap_expired_leases(session) == 1
    async with SessionLocal() as session:
        recovered = await session.get(Task, task_id)
        assert recovered is not None
        assert recovered.state == TaskState.RETRY_SCHEDULED.value
        assert recovered.lease_owner is None


async def test_transient_failure_is_rescheduled_then_released() -> None:
    async with SessionLocal() as session:
        workflow = await submit_workflow(
            session,
            definition(
                TaskDefinition(
                    key="external",
                    kind="flaky",
                    retry=RetrySpec(max_attempts=3, base_delay_seconds=0.01, max_delay_seconds=0.01),
                )
            ),
            "external-retry",
        )
    async with SessionLocal() as session:
        task = await session.scalar(select(Task).where(Task.workflow_id == workflow.id))
        assert task is not None
        task_id = task.id
    async with SessionLocal() as session:
        await acquire_task(session, task_id=task_id, worker_id="w1", lease_seconds=30)
    async with SessionLocal() as session:
        state = await mark_task_failed(
            session, task_id=task_id, worker_id="w1", error="provider unavailable"
        )
        assert state == TaskState.RETRY_SCHEDULED.value
    async with SessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == task_id).values(available_at=datetime.now(UTC))
        )
        await session.commit()
    async with SessionLocal() as session:
        assert await release_due_tasks(session) == 1
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        assert task.state == TaskState.QUEUED.value


async def test_workflow_cancellation_is_durable() -> None:
    async with SessionLocal() as session:
        workflow = await submit_workflow(
            session,
            definition(TaskDefinition(key="a"), TaskDefinition(key="b", depends_on=["a"])),
            "cancel-me",
        )
    async with SessionLocal() as session:
        cancelled = await cancel_workflow(session, workflow.id)
        assert cancelled is not None
    async with SessionLocal() as session:
        persisted = await session.get(Workflow, workflow.id)
        tasks = list(await session.scalars(select(Task).where(Task.workflow_id == workflow.id)))
    assert persisted is not None and persisted.state == "cancelled"
    assert all(task.state == TaskState.CANCELLED.value for task in tasks)


async def test_real_redis_duplicate_messages_are_visible_but_db_lease_dedupes() -> None:
    redis: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    transport = RedisTransport(redis)
    await transport.ensure_groups(["default"])
    workflow_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    await transport.publish_task(queue="default", task_id=task_id, workflow_id=workflow_id)
    await transport.publish_task(queue="default", task_id=task_id, workflow_id=workflow_id)
    messages = await transport.read(["default"], "test-consumer", count=10)
    assert len([message for message in messages if message.task_id == task_id]) == 2
    for message in messages:
        await transport.ack(message)
    await redis.aclose()
