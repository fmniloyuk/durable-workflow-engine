from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings

_configured = False


def configure_telemetry(*, engine: AsyncEngine, app: FastAPI | None = None) -> None:
    global _configured
    if _configured:
        if app is not None:
            FastAPIInstrumentor.instrument_app(app)
        return

    settings = get_settings()
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: settings.service_name}))
    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint,
            insecure=settings.otel_exporter_otlp_endpoint.startswith("http://"),
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    RedisInstrumentor().instrument()
    if app is not None:
        FastAPIInstrumentor.instrument_app(app)
    _configured = True


def tracer(name: str = "durable-workflow-engine") -> Any:
    return trace.get_tracer(name)
