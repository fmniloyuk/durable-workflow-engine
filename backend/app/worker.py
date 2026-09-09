import asyncio
import os
import signal
import socket
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from opentelemetry.propagate import extract
from prometheus_client import start_http_server
from redis.asyncio import Redis

from app.config import get_settings
from app.db import SessionLocal, engine
from app.engine import (
    acquire_task,
    heartbeat_worker,
    mark_task_failed,
    mark_task_succeeded,
    register_worker,
    renew_lease,
    set_worker_draining,
)
from app.metrics import RETRIES, TASK_LATENCY, TASK_OUTCOMES, WORKER_UTILIZATION
from app.models import Task, TaskState
from app.queue import QueueGuards, QueueMessage, RedisTransport
from app.telemetry import configure_telemetry, tracer

TaskHandler = Callable[[Task], Awaitable[dict[str, Any]]]


async def noop_handler(task: Task) -> dict[str, Any]:
    await asyncio.sleep(float(task.payload.get("sleep_seconds", 0)))
    return {"ok": True, "task": task.task_key, "payload": task.payload}


async def sleep_handler(task: Task) -> dict[str, Any]:
    seconds = min(float(task.payload.get("seconds", 1)), 300.0)
    await asyncio.sleep(max(0.0, seconds))
    return {"slept_seconds": seconds}


async def flaky_handler(task: Task) -> dict[str, Any]:
    fail_until_attempt = int(task.payload.get("fail_until_attempt", 1))
    if task.attempt <= fail_until_attempt:
        raise RuntimeError(f"injected transient failure on attempt {task.attempt}")
    return {"ok": True, "recovered_on_attempt": task.attempt}


HANDLERS: dict[str, TaskHandler] = {
    "noop": noop_handler,
    "sleep": sleep_handler,
    "flaky": flaky_handler,
}


class WorkerRuntime:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.worker_id = self.settings.worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.redis: Redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
        self.transport = RedisTransport(self.redis)
        self.guards = QueueGuards(self.redis)
        self.stop = asyncio.Event()
        self.semaphore = asyncio.Semaphore(self.settings.worker_concurrency)
        self.active = 0
        self.running: set[asyncio.Task[None]] = set()

    async def heartbeat_loop(self) -> None:
        while not self.stop.is_set():
            async with SessionLocal() as session:
                await heartbeat_worker(session, self.worker_id)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.settings.heartbeat_seconds)
            except TimeoutError:
                pass

    async def renew_loop(
        self, task: Task, done: asyncio.Event, lease_lost: asyncio.Event
    ) -> None:
        interval = max(1.0, self.settings.heartbeat_seconds / 2)
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=interval)
                break
            except TimeoutError:
                async with SessionLocal() as session:
                    renewed = await renew_lease(
                        session,
                        task_id=task.id,
                        worker_id=self.worker_id,
                        lease_seconds=self.settings.lease_seconds,
                    )
                if not renewed:
                    lease_lost.set()
                    return
                await self.guards.renew_concurrency(task.queue, str(task.id))

    async def execute(self, task: Task) -> tuple[bool, dict[str, Any] | str]:
        handler = HANDLERS.get(task.kind)
        if handler is None:
            return False, f"unknown task kind: {task.kind}"
        try:
            with TASK_LATENCY.labels(queue=task.queue, kind=task.kind).time():
                async with asyncio.timeout(task.timeout_seconds):
                    result = await handler(task)
            return True, result
        except TimeoutError:
            return False, f"task timed out after {task.timeout_seconds}s"
        except Exception as exc:  # task code is an isolation boundary
            return False, f"{type(exc).__name__}: {exc}"

    async def process_message(self, message: QueueMessage) -> None:
        async with self.semaphore:
            await self.guards.wait_for_rate_token(message.queue)
            await self.guards.acquire_concurrency(message.queue, message.task_id)
            self.active += 1
            WORKER_UTILIZATION.labels(worker=self.worker_id).set(
                self.active / self.settings.worker_concurrency
            )
            task: Task | None = None
            try:
                async with SessionLocal() as session:
                    task = await acquire_task(
                        session,
                        task_id=uuid.UUID(message.task_id),
                        worker_id=self.worker_id,
                        lease_seconds=self.settings.lease_seconds,
                    )
                if task is None:
                    await self.transport.ack(message)
                    return

                done = asyncio.Event()
                lease_lost = asyncio.Event()
                renewer = asyncio.create_task(self.renew_loop(task, done, lease_lost))
                context = extract({k: v for k, v in message.headers.items() if v})
                try:
                    with tracer().start_as_current_span(
                        "workflow.task.execute",
                        context=context,
                        attributes={
                            "workflow.id": str(task.workflow_id),
                            "task.id": str(task.id),
                            "task.key": task.task_key,
                            "task.queue": task.queue,
                            "task.attempt": task.attempt,
                        },
                    ):
                        ok, value = await self.execute(task)
                finally:
                    done.set()
                    await renewer

                if lease_lost.is_set():
                    return

                if ok:
                    assert isinstance(value, dict)
                    async with SessionLocal() as session:
                        committed = await mark_task_succeeded(
                            session,
                            task_id=task.id,
                            worker_id=self.worker_id,
                            result=value,
                        )
                    if committed:
                        TASK_OUTCOMES.labels(queue=task.queue, outcome="succeeded").inc()
                else:
                    assert isinstance(value, str)
                    async with SessionLocal() as session:
                        state = await mark_task_failed(
                            session,
                            task_id=task.id,
                            worker_id=self.worker_id,
                            error=value,
                        )
                    if state == TaskState.RETRY_SCHEDULED.value:
                        RETRIES.labels(queue=task.queue).inc()
                    else:
                        TASK_OUTCOMES.labels(queue=task.queue, outcome=state).inc()
                await self.transport.ack(message)
            finally:
                self.active -= 1
                WORKER_UTILIZATION.labels(worker=self.worker_id).set(
                    self.active / self.settings.worker_concurrency
                )
                await self.guards.release_concurrency(message.queue, message.task_id)

    async def run(self) -> None:
        configure_telemetry(engine=engine)
        start_http_server(self.settings.metrics_port)
        await self.transport.ensure_groups(self.settings.queues)
        async with SessionLocal() as session:
            await register_worker(
                session,
                worker_id=self.worker_id,
                queues=self.settings.queues,
                concurrency=self.settings.worker_concurrency,
            )
        heartbeat_task = asyncio.create_task(self.heartbeat_loop())
        try:
            while not self.stop.is_set():
                messages = await self.transport.read(
                    self.settings.queues,
                    self.worker_id,
                    count=self.settings.worker_concurrency,
                )
                for message in messages:
                    task = asyncio.create_task(self.process_message(message))
                    self.running.add(task)
                    task.add_done_callback(self.running.discard)
        finally:
            self.stop.set()
            async with SessionLocal() as session:
                await set_worker_draining(session, self.worker_id)
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            if self.running:
                _, pending = await asyncio.wait(
                    self.running, timeout=max(1.0, self.settings.lease_seconds / 2)
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            await self.redis.aclose()


def main() -> None:
    runtime = WorkerRuntime()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def request_shutdown() -> None:
        runtime.stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_shutdown)
    try:
        loop.run_until_complete(runtime.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
