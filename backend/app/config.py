from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://workflow:workflow@localhost:5432/workflow"
    redis_url: str = "redis://localhost:6379/0"
    service_name: str = "durable-workflow-engine"
    lease_seconds: int = Field(default=30, ge=2)
    heartbeat_seconds: int = Field(default=10, ge=1)
    outbox_poll_seconds: float = Field(default=0.5, gt=0)
    scheduler_poll_seconds: float = Field(default=1.0, gt=0)
    queue_partitions: int = Field(default=4, ge=1, le=64)
    worker_id: str = "worker-local"
    worker_queues: str = "default,billing,email,analytics"
    worker_concurrency: int = Field(default=8, ge=1, le=256)
    queue_concurrency_limit: int = Field(default=32, ge=1)
    queue_rate_per_second: float = Field(default=100.0, gt=0)
    metrics_port: int = Field(default=9101, ge=1, le=65535)
    otel_exporter_otlp_endpoint: str | None = None

    @property
    def queues(self) -> list[str]:
        return [q.strip() for q in self.worker_queues.split(",") if q.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
