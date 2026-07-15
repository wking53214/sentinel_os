"""OpenTelemetry tracing for Sentinel OS.

Safe default: if OTEL_EXPORTER_OTLP_ENDPOINT is not set (or if the
opentelemetry packages aren't installed), every span call is a no-op —
zero overhead, zero crashes, zero dependency on a running collector.
Set OTEL_EXPORTER_OTLP_ENDPOINT to a collector URL (e.g.
http://localhost:4317) to activate real tracing.

Usage in the hot path:
    from tracing import tracer
    with tracer.start_as_current_span("process_call") as span:
        span.set_attribute("call.sid", sid)
        ...
"""
import os

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.resources import Resource

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    if endpoint:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        resource = Resource.create({"service.name": "sentinel-os"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        print(f"✓ OpenTelemetry tracing enabled (exporting to {endpoint})")
    else:
        # No endpoint → use the default no-op provider. Every
        # tracer.start_as_current_span() call returns a non-recording
        # span that does nothing — zero overhead, zero side effects.
        pass

    tracer = trace.get_tracer("sentinel_os")

    def mark_error(span, description: str = "") -> None:
        """Mark a span ERROR, so it surfaces in an error-filtered trace
        query even in a collector that doesn't infer status from
        record_exception() alone. Callers don't need to know whether
        real OpenTelemetry is installed -- see the no-op version below.
        """
        span.set_status(trace.Status(trace.StatusCode.ERROR, description))

except ImportError:
    # opentelemetry packages not installed at all — provide a stub
    # tracer whose start_as_current_span is a trivial context manager
    # so instrumented code doesn't need to care.
    from contextlib import contextmanager

    class _NoOpTracer:
        @contextmanager
        def start_as_current_span(self, name, **kwargs):
            yield _NoOpSpan()

    class _NoOpSpan:
        def set_attribute(self, key, value): pass
        def set_status(self, status): pass
        def record_exception(self, exc): pass

    tracer = _NoOpTracer()

    def mark_error(span, description: str = "") -> None:
        pass
