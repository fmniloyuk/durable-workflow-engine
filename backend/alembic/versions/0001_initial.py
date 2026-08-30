"""Initial durable workflow schema.

Revision ID: 0001
Revises:
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_workflows_state", "workflows", ["state"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("task_key", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("queue", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("workflow_id", "task_key", name="uq_task_workflow_key"),
    )
    op.create_index("ix_tasks_workflow_id", "tasks", ["workflow_id"])
    op.create_index("ix_tasks_queue", "tasks", ["queue"])
    op.create_index("ix_tasks_state", "tasks", ["state"])
    op.create_index("ix_tasks_lease_owner", "tasks", ["lease_owner"])
    op.create_index("ix_tasks_reap", "tasks", ["state", "lease_expires_at"])
    op.create_index("ix_tasks_schedule", "tasks", ["state", "available_at"])

    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_events_workflow_created", "task_events", ["workflow_id", "created_at"])

    op.create_table(
        "workers",
        sa.Column("id", sa.String(length=200), nullable=False),
        sa.Column("queues", sa.JSON(), nullable=False),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workers_state", "workers", ["state"])

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_events_event_type", "outbox_events", ["event_type"])
    op.create_index("ix_outbox_unpublished", "outbox_events", ["published_at", "created_at"])

    op.create_table(
        "effect_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("effect_name", sa.String(length=100), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("effect_records")
    op.drop_index("ix_outbox_unpublished", table_name="outbox_events")
    op.drop_index("ix_outbox_events_event_type", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_workers_state", table_name="workers")
    op.drop_table("workers")
    op.drop_index("ix_task_events_workflow_created", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("ix_tasks_schedule", table_name="tasks")
    op.drop_index("ix_tasks_reap", table_name="tasks")
    op.drop_index("ix_tasks_lease_owner", table_name="tasks")
    op.drop_index("ix_tasks_state", table_name="tasks")
    op.drop_index("ix_tasks_queue", table_name="tasks")
    op.drop_index("ix_tasks_workflow_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_workflows_state", table_name="workflows")
    op.drop_table("workflows")
