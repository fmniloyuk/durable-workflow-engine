import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RetrySpec(BaseModel):
    max_attempts: int = Field(default=5, ge=1, le=100)
    base_delay_seconds: float = Field(default=1.0, ge=0.01, le=3600)
    max_delay_seconds: float = Field(default=300.0, ge=0.01, le=86400)


class TaskDefinition(BaseModel):
    key: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.-]+$")
    kind: str = Field(default="noop", min_length=1, max_length=100)
    queue: str = Field(default="default", min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    retry: RetrySpec = Field(default_factory=RetrySpec)
    timeout_seconds: int = Field(default=60, ge=1, le=86400)
    schedule_delay_seconds: float = Field(default=0, ge=0, le=604800)


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tasks: list[TaskDefinition] = Field(min_length=1, max_length=1000)


class TaskView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_key: str
    kind: str
    queue: str
    state: str
    dependencies: list[str]
    attempt: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    result: dict[str, Any] | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class EventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: uuid.UUID | None
    event_type: str
    details: dict[str, Any]
    created_at: datetime


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    state: str
    created_at: datetime
    updated_at: datetime


class WorkflowDetail(WorkflowSummary):
    tasks: list[TaskView]
    timeline: list[EventView]


class WorkerView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    queues: list[str]
    concurrency: int
    state: str
    registered_at: datetime
    heartbeat_at: datetime


class CancelResponse(BaseModel):
    workflow_id: uuid.UUID
    state: str
