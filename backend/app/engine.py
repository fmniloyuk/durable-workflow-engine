import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dag import validate_dag
from app.models import OutboxEvent, Task, TaskEvent, TaskState, Worker, WorkerState, Workflow, WorkflowState
from app.retry import full_jitter_delay
from app.schemas import WorkflowCreate

EXECUTABLE_STATES = (TaskState.QUEUED.value,)
TERMINAL_TASK_STATES = {
    TaskState.SUCCEEDED.value,
    TaskState.FAILED.value,
    TaskState.CANCELLED.value,
    TaskState.DEAD_LETTER.value,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _ready_event(task: Task) -> OutboxEvent:
    return OutboxEvent(
        aggregate_type="task",
        aggregate_id=str(task.id),
        event_type="task.ready",
        payload={
            "task_id": str(task.id),
            "workflow_id": str(task.workflow_id),
            "queue": task.queue,
        },
    )


def _event(task: Task, event_type: str, **details: Any) -> TaskEvent:
    return TaskEvent(
        workflow_id=task.workflow_id,
        task_id=task.id,
        event_type=event_type,
        details=details,
    )


async def submit_workflow(
    session: AsyncSession, definition: WorkflowCreate, idempotency_key: str | None
) -> Workflow:
    validate_dag(definition.tasks)
    if idempotency_key:
        existing = await session.scalar(
            select(Workflow).where(Workflow.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing

    now = utcnow()
    workflow = Workflow(
        name=definition.name,
        state=WorkflowState.RUNNING.value,
        idempotency_key=idempotency_key,
        definition=definition.model_dump(mode="json"),
    )
    session.add(workflow)
    await session.flush()

    task_rows: list[Task] = []
    for item in definition.tasks:
        available_at = now + timedelta(seconds=item.schedule_delay_seconds)
        if item.depends_on:
            state = TaskState.PENDING.value
        elif item.schedule_delay_seconds > 0:
            state = "scheduled"
        else:
            state = TaskState.QUEUED.value
        effect_key = f"{idempotency_key or workflow.id}:{item.key}"
        task = Task(
            workflow_id=workflow.id,
            task_key=item.key,
            kind=item.kind,
            queue=item.queue,
            state=state,
            payload={
                **item.payload,
                "_retry_base_delay_seconds": item.retry.base_delay_seconds,
                "_retry_max_delay_seconds": item.retry.max_delay_seconds,
            },
            dependencies=item.depends_on,
            max_attempts=item.retry.max_attempts,
            timeout_seconds=item.timeout_seconds,
            available_at=available_at,
            idempotency_key=effect_key,
        )
        session.add(task)
        task_rows.append(task)
    await session.flush()

    session.add(
        TaskEvent(
            workflow_id=workflow.id,
            task_id=None,
            event_type="workflow.created",
            details={"name": workflow.name},
        )
    )
    for task in task_rows:
        if task.state == TaskState.QUEUED.value:
            session.add(_ready_event(task))
            session.add(_event(task, "task.queued"))
        elif task.state == "scheduled":
            session.add(_event(task, "task.scheduled", available_at=task.available_at.isoformat()))

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if not idempotency_key:
            raise
        existing = await session.scalar(
            select(Workflow).where(Workflow.idempotency_key == idempotency_key)
        )
        if existing is None:
            raise
        return existing
    await session.refresh(workflow)
    return workflow


async def workflow_query(workflow_id: uuid.UUID) -> Select[tuple[Workflow]]:
    return select(Workflow).where(Workflow.id == workflow_id).options(selectinload(Workflow.tasks))


async def acquire_task(
    session: AsyncSession, *, task_id: uuid.UUID, worker_id: str, lease_seconds: int
) -> Task | None:
    now = utcnow()
    stmt = (
        update(Task)
        .where(
            Task.id == task_id,
            Task.state.in_(EXECUTABLE_STATES),
            Task.available_at <= now,
            or_(Task.lease_expires_at.is_(None), Task.lease_expires_at < now),
        )
        .values(
            state=TaskState.RUNNING.value,
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            started_at=func.coalesce(Task.started_at, now),
            attempt=Task.attempt + 1,
            error=None,
        )
        .returning(Task)
    )
    result = await session.execute(stmt)
    task = result.scalar_one_or_none()
    if task is not None:
        session.add(_event(task, "task.leased", worker_id=worker_id, attempt=task.attempt))
    await session.commit()
    return task


async def renew_lease(
    session: AsyncSession, *, task_id: uuid.UUID, worker_id: str, lease_seconds: int
) -> bool:
    result = await session.execute(
        update(Task)
        .where(
            Task.id == task_id,
            Task.state == TaskState.RUNNING.value,
            Task.lease_owner == worker_id,
        )
        .values(lease_expires_at=utcnow() + timedelta(seconds=lease_seconds))
    )
    await session.commit()
    return bool(result.rowcount)


async def _lock_workflow_tasks(session: AsyncSession, workflow_id: uuid.UUID) -> list[Task]:
    rows = await session.scalars(
        select(Task).where(Task.workflow_id == workflow_id).with_for_update()
    )
    return list(rows)


async def _recompute_workflow(session: AsyncSession, workflow: Workflow, tasks: list[Task]) -> None:
    states = {task.state for task in tasks}
    if workflow.state == WorkflowState.CANCELLED.value:
        return
    if TaskState.DEAD_LETTER.value in states or TaskState.FAILED.value in states:
        workflow.state = WorkflowState.FAILED.value
    elif states and states <= {TaskState.SUCCEEDED.value}:
        workflow.state = WorkflowState.SUCCEEDED.value
        session.add(
            TaskEvent(
                workflow_id=workflow.id,
                task_id=None,
                event_type="workflow.succeeded",
                details={},
            )
        )
    else:
        workflow.state = WorkflowState.RUNNING.value


async def mark_task_succeeded(
    session: AsyncSession, *, task_id: uuid.UUID, worker_id: str, result: dict[str, Any]
) -> bool:
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None or task.state != TaskState.RUNNING.value or task.lease_owner != worker_id:
        await session.rollback()
        return False
    workflow = await session.scalar(select(Workflow).where(Workflow.id == task.workflow_id).with_for_update())
    if workflow is None:
        await session.rollback()
        return False
    tasks = await _lock_workflow_tasks(session, task.workflow_id)
    if workflow.state == WorkflowState.CANCELLED.value:
        task.state = TaskState.CANCELLED.value
        task.lease_owner = None
        task.lease_expires_at = None
        session.add(_event(task, "task.cancelled_after_execution"))
        await session.commit()
        return False

    task.state = TaskState.SUCCEEDED.value
    task.result = result
    task.finished_at = utcnow()
    task.lease_owner = None
    task.lease_expires_at = None
    session.add(_event(task, "task.succeeded", attempt=task.attempt))

    by_key = {row.task_key: row for row in tasks}
    for candidate in tasks:
        if candidate.state != TaskState.PENDING.value:
            continue
        if task.task_key not in candidate.dependencies:
            continue
        if all(by_key[dependency].state == TaskState.SUCCEEDED.value for dependency in candidate.dependencies):
            if candidate.available_at <= utcnow():
                candidate.state = TaskState.QUEUED.value
                session.add(_ready_event(candidate))
                session.add(_event(candidate, "task.queued", reason="dependencies_satisfied"))
            else:
                candidate.state = "scheduled"
                session.add(
                    _event(candidate, "task.scheduled", available_at=candidate.available_at.isoformat())
                )

    await _recompute_workflow(session, workflow, tasks)
    await session.commit()
    return True


async def mark_task_failed(
    session: AsyncSession,
    *,
    task_id: uuid.UUID,
    worker_id: str,
    error: str,
) -> str:
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None or task.state != TaskState.RUNNING.value or task.lease_owner != worker_id:
        await session.rollback()
        return "lost_lease"
    workflow = await session.scalar(select(Workflow).where(Workflow.id == task.workflow_id).with_for_update())
    if workflow is None:
        await session.rollback()
        return "missing_workflow"

    task.error = error[:8000]
    task.lease_owner = None
    task.lease_expires_at = None
    if workflow.state == WorkflowState.CANCELLED.value:
        task.state = TaskState.CANCELLED.value
        task.finished_at = utcnow()
        session.add(_event(task, "task.cancelled"))
        await session.commit()
        return task.state

    if task.attempt >= task.max_attempts:
        task.state = TaskState.DEAD_LETTER.value
        task.finished_at = utcnow()
        session.add(_event(task, "task.dead_lettered", attempt=task.attempt, error=task.error))
        session.add(
            OutboxEvent(
                aggregate_type="task",
                aggregate_id=str(task.id),
                event_type="task.dead_lettered",
                payload={
                    "task_id": str(task.id),
                    "workflow_id": str(task.workflow_id),
                    "queue": task.queue,
                    "error": task.error,
                    "attempt": task.attempt,
                },
            )
        )
        tasks = await _lock_workflow_tasks(session, task.workflow_id)
        await _recompute_workflow(session, workflow, tasks)
    else:
        retry_number = max(1, task.attempt)
        delay = full_jitter_delay(
            retry_number,
            base_delay_seconds=float(task.payload.get("_retry_base_delay_seconds", 1.0)),
            max_delay_seconds=float(task.payload.get("_retry_max_delay_seconds", 300.0)),
        )
        task.state = TaskState.RETRY_SCHEDULED.value
        task.available_at = utcnow() + timedelta(seconds=delay)
        session.add(
            _event(
                task,
                "task.retry_scheduled",
                attempt=task.attempt,
                delay_seconds=delay,
                error=task.error,
            )
        )
    await session.commit()
    return task.state


async def cancel_workflow(session: AsyncSession, workflow_id: uuid.UUID) -> Workflow | None:
    workflow = await session.scalar(select(Workflow).where(Workflow.id == workflow_id).with_for_update())
    if workflow is None:
        return None
    if workflow.state in {WorkflowState.SUCCEEDED.value, WorkflowState.FAILED.value}:
        return workflow
    workflow.state = WorkflowState.CANCELLED.value
    workflow.cancelled_at = utcnow()
    await session.execute(
        update(Task)
        .where(
            Task.workflow_id == workflow_id,
            Task.state.in_([
                TaskState.PENDING.value,
                TaskState.QUEUED.value,
                TaskState.RETRY_SCHEDULED.value,
                "scheduled",
            ]),
        )
        .values(state=TaskState.CANCELLED.value, finished_at=utcnow())
    )
    session.add(
        TaskEvent(
            workflow_id=workflow.id,
            task_id=None,
            event_type="workflow.cancelled",
            details={},
        )
    )
    await session.commit()
    return workflow


async def register_worker(
    session: AsyncSession, *, worker_id: str, queues: list[str], concurrency: int
) -> None:
    existing = await session.get(Worker, worker_id)
    now = utcnow()
    if existing is None:
        session.add(
            Worker(
                id=worker_id,
                queues=queues,
                concurrency=concurrency,
                state=WorkerState.ACTIVE.value,
                heartbeat_at=now,
            )
        )
    else:
        existing.queues = queues
        existing.concurrency = concurrency
        existing.state = WorkerState.ACTIVE.value
        existing.heartbeat_at = now
    await session.commit()


async def heartbeat_worker(session: AsyncSession, worker_id: str) -> None:
    await session.execute(
        update(Worker)
        .where(Worker.id == worker_id)
        .values(heartbeat_at=utcnow(), state=WorkerState.ACTIVE.value)
    )
    await session.commit()


async def set_worker_draining(session: AsyncSession, worker_id: str) -> None:
    await session.execute(
        update(Worker).where(Worker.id == worker_id).values(state=WorkerState.DRAINING.value)
    )
    await session.commit()


async def mark_stale_workers(session: AsyncSession, stale_after_seconds: int) -> int:
    result = await session.execute(
        update(Worker)
        .where(Worker.heartbeat_at < utcnow() - timedelta(seconds=stale_after_seconds))
        .values(state=WorkerState.STALE.value)
    )
    await session.commit()
    return int(result.rowcount or 0)


async def release_due_tasks(session: AsyncSession, *, limit: int = 200) -> int:
    due = list(
        await session.scalars(
            select(Task)
            .where(Task.state.in_([TaskState.RETRY_SCHEDULED.value, "scheduled"]), Task.available_at <= utcnow())
            .order_by(Task.available_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for task in due:
        task.state = TaskState.QUEUED.value
        session.add(_ready_event(task))
        session.add(_event(task, "task.queued", reason="schedule_due"))
    await session.commit()
    return len(due)


async def reap_expired_leases(session: AsyncSession, *, limit: int = 200) -> int:
    expired = list(
        await session.scalars(
            select(Task)
            .where(Task.state == TaskState.RUNNING.value, Task.lease_expires_at < utcnow())
            .order_by(Task.lease_expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    now = utcnow()
    for task in expired:
        task.lease_owner = None
        task.lease_expires_at = None
        task.error = "worker lease expired"
        if task.attempt >= task.max_attempts:
            task.state = TaskState.DEAD_LETTER.value
            task.finished_at = now
            session.add(_event(task, "task.dead_lettered", reason="lease_expired"))
            session.add(
                OutboxEvent(
                    aggregate_type="task",
                    aggregate_id=str(task.id),
                    event_type="task.dead_lettered",
                    payload={
                        "task_id": str(task.id),
                        "workflow_id": str(task.workflow_id),
                        "queue": task.queue,
                        "error": task.error,
                        "attempt": task.attempt,
                    },
                )
            )
        else:
            delay = full_jitter_delay(
                max(1, task.attempt),
                base_delay_seconds=float(task.payload.get("_retry_base_delay_seconds", 1.0)),
                max_delay_seconds=float(task.payload.get("_retry_max_delay_seconds", 300.0)),
            )
            task.state = TaskState.RETRY_SCHEDULED.value
            task.available_at = now + timedelta(seconds=delay)
            session.add(
                _event(task, "task.lease_expired", retry_delay_seconds=delay, attempt=task.attempt)
            )
    await session.commit()
    return len(expired)


async def republish_orphaned_queued(
    session: AsyncSession, *, older_than_seconds: int, limit: int = 200
) -> int:
    threshold = utcnow() - timedelta(seconds=older_than_seconds)
    tasks = list(
        await session.scalars(
            select(Task)
            .where(Task.state == TaskState.QUEUED.value, Task.updated_at < threshold)
            .order_by(Task.updated_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for task in tasks:
        task.updated_at = utcnow()
        session.add(_ready_event(task))
        session.add(_event(task, "task.republished", reason="orphan_queue_recovery"))
    await session.commit()
    return len(tasks)
