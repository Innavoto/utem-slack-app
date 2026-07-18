"""UTEM distributed tracing — canonical drop-in (OTel → OTLP → collector → Tempo).

Copy to each FastAPI service as ``app/core/tracing.py`` and call once after the
app is created::

    from app.core.tracing import setup_tracing
    app = FastAPI(...)
    setup_tracing(app, service_name="utem-<svc>")

Fully best-effort: never raises, no-ops if OTEL_TRACES_ENABLED=false or if the
OpenTelemetry packages aren't installed. Backend-agnostic — the exporter always
targets the in-cluster OTel Collector; swapping Tempo for Cloud Trace/S3/etc. is
a Collector/Tempo config change, never a service change.

Env:
  OTEL_TRACES_ENABLED           default "true"
  OTEL_SERVICE_NAME             overrides service_name arg
  OTEL_EXPORTER_OTLP_ENDPOINT   default the in-cluster collector (gRPC :4317)
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "http://otel-collector.utem-observability.svc.cluster.local:4317"


def setup_tracing(app=None, service_name: str | None = None) -> None:
    if os.getenv("OTEL_TRACES_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:  # noqa: BLE001 — deps missing → tracing disabled, never break the app
        log.warning("tracing disabled (otel sdk import failed): %s", exc)
        return

    try:
        name = service_name or os.getenv("OTEL_SERVICE_NAME") or "utem-service"
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", _DEFAULT_ENDPOINT)
        provider = TracerProvider(resource=Resource.create({"service.name": name}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
        trace.set_tracer_provider(provider)
        log.info("tracing enabled: service=%s endpoint=%s", name, endpoint)
    except Exception as exc:  # noqa: BLE001
        log.warning("tracing provider setup skipped: %s", exc)
        return

    _instrument_fastapi(app)
    # Each auto-instrumentor is guarded independently — a missing lib is fine.
    for module, cls in (
        ("httpx", "HTTPXClientInstrumentor"),
        ("requests", "RequestsInstrumentor"),
        ("sqlalchemy", "SQLAlchemyInstrumentor"),
        ("asyncpg", "AsyncPGInstrumentor"),
        ("redis", "RedisInstrumentor"),
        ("logging", "LoggingInstrumentor"),
    ):
        try:
            m = __import__(f"opentelemetry.instrumentation.{module}", fromlist=[cls])
            getattr(m, cls)().instrument()
        except Exception:  # noqa: BLE001,silent-except  best-effort: a missing instr lib must not break the app
            pass


def _instrument_fastapi(app) -> None:
    if app is None:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:  # noqa: BLE001,silent-except  best-effort: fastapi instr optional
        pass
