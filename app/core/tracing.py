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
    _install_deep_span_middleware(app, name)
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


def _install_deep_span_middleware(app, service_name: str) -> None:
    """Emit one INTERNAL 'deep' span per request named ``<svc>.<operation>``.

    The auto FastAPI/redis/httpx instrumentors only produce SERVER/CLIENT spans
    plus generic 'http send' INTERNAL spans — none match the dotted manual-span
    convention (``<svc>.<op>``) the 'Deep code-level spans' dashboards query
    (span_kind=INTERNAL, name ~ ``[a-z][a-z0-9-]*\\.[a-z0-9_.-]+``). This gives
    every service a per-operation deep span so those panels populate, and is the
    anchor services graft finer ``deep_span("svc.subop")`` calls onto.
    """
    if app is None:
        return
    short = service_name.split("utem-", 1)[-1] or "svc"  # utem-certwatch -> certwatch
    try:
        from opentelemetry import trace
        from opentelemetry.trace import SpanKind

        tracer = trace.get_tracer("utem.deepspan")

        # Pure ASGI middleware — does NOT buffer the response, so it is safe with
        # StreamingResponse / SSE / BackgroundTasks (unlike Starlette BaseHTTPMiddleware).
        class _DeepSpanASGI:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope.get("type") != "http":
                    return await self.app(scope, receive, send)
                with tracer.start_as_current_span(
                    f"{short}.request", kind=SpanKind.INTERNAL
                ) as span:
                    async def _send(message):
                        if message.get("type") == "http.response.start":
                            try:
                                span.set_attribute("http.status_code", message.get("status", 0))
                                route = scope.get("route")
                                op = getattr(route, "name", None) or getattr(
                                    getattr(route, "endpoint", None), "__name__", None
                                )
                                if op:
                                    span.update_name(f"{short}.{op}")
                            except Exception:  # noqa: BLE001,silent-except  best-effort span enrichment
                                pass
                        await send(message)

                    await self.app(scope, receive, _send)

        app.add_middleware(_DeepSpanASGI)
    except Exception:  # noqa: BLE001,silent-except  best-effort: middleware optional
        pass


# ── Public helpers for hand-authored business-logic spans ──────────────────────
def deep_span(name: str):
    """Context manager for a manual INTERNAL span: ``with deep_span("svc.op"): ...``."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        try:
            from opentelemetry import trace
            from opentelemetry.trace import SpanKind

            with trace.get_tracer("utem.deepspan").start_as_current_span(
                name, kind=SpanKind.INTERNAL
            ):
                yield
        except Exception:  # noqa: BLE001,silent-except  best-effort: tracing must never break business logic
            yield

    return _cm()


def traced(name: str):
    """Decorator wrapping a sync/async function in a manual INTERNAL span ``name``."""
    import functools
    import inspect

    def deco(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrap(*a, **k):
                with deep_span(name):
                    return await fn(*a, **k)
            return awrap

        @functools.wraps(fn)
        def wrap(*a, **k):
            with deep_span(name):
                return fn(*a, **k)
        return wrap

    return deco
