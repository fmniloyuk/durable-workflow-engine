import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.dag import DAGValidationError
from app.db import engine, get_session
from app.engine import cancel_workflow, submit_workflow
from app.models import Task, TaskEvent, TaskState, Worker, Workflow
from app.schemas import (
    CancelResponse,
    EventView,
    TaskView,
    WorkerView,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowSummary,
)
from app.telemetry import configure_telemetry

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_telemetry(engine=engine, app=app)
    yield


app = FastAPI(
    title="durable-workflow-engine",
    version="0.1.0",
    description="A small durable workflow engine demonstrating distributed-systems correctness.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.mount("/metrics", make_asgi_app())
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@app.exception_handler(DAGValidationError)
async def dag_error_handler(_request: object, exc: DAGValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/healthz")
async def healthz(session: SessionDep) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.ping()
    finally:
        await redis.aclose()
    return {"status": "ok"}


@app.post("/v1/workflows", response_model=WorkflowSummary, status_code=202)
async def create_workflow(
    body: WorkflowCreate,
    session: SessionDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Workflow:
    if idempotency_key is not None and len(idempotency_key) > 255:
        raise HTTPException(status_code=400, detail="Idempotency-Key must be <= 255 characters")
    return await submit_workflow(session, body, idempotency_key)


@app.get("/v1/workflows", response_model=list[WorkflowSummary])
async def list_workflows(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[Workflow]:
    rows = await session.scalars(select(Workflow).order_by(Workflow.created_at.desc()).limit(limit))
    return list(rows)


@app.get("/v1/workflows/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(workflow_id: uuid.UUID, session: SessionDep) -> WorkflowDetail:
    workflow = await session.scalar(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.tasks))
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    events = list(
        await session.scalars(
            select(TaskEvent)
            .where(TaskEvent.workflow_id == workflow_id)
            .order_by(TaskEvent.created_at, TaskEvent.id)
        )
    )
    return WorkflowDetail(
        id=workflow.id,
        name=workflow.name,
        state=workflow.state,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        tasks=[TaskView.model_validate(task) for task in workflow.tasks],
        timeline=[EventView.model_validate(event) for event in events],
    )


@app.post("/v1/workflows/{workflow_id}/cancel", response_model=CancelResponse)
async def cancel(workflow_id: uuid.UUID, session: SessionDep) -> CancelResponse:
    workflow = await cancel_workflow(session, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return CancelResponse(workflow_id=workflow.id, state=workflow.state)


@app.get("/v1/workers", response_model=list[WorkerView])
async def list_workers(session: SessionDep) -> list[Worker]:
    rows = await session.scalars(select(Worker).order_by(Worker.heartbeat_at.desc()))
    return list(rows)


@app.get("/v1/dead-letter", response_model=list[TaskView])
async def dead_letter_tasks(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[Task]:
    rows = await session.scalars(
        select(Task)
        .where(Task.state == TaskState.DEAD_LETTER.value)
        .order_by(Task.finished_at.desc())
        .limit(limit)
    )
    return list(rows)
