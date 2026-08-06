"""OpenTelemetry tracing, with PHI scrubbed at the source (span
attributes), not the sink -- per CLAUDE.md rule 6 and the Phase 8 prompt.

No auto-instrumentation (`opentelemetry-instrumentation-fastapi`,
`-sqlalchemy`, etc.) is used anywhere in this codebase: auto-instrumentors
can capture raw SQL text or route parameters as span attributes, an
uncontrolled leak surface neither this module nor `PHIRedactionFilter`
was built to police. Every span in this system is created manually, with
attributes the calling code explicitly chooses -- tenant/finding/
remittance ids, counts, status strings, never a patient field.
`PHIScrubbingSpanExporter` is defense in depth on top of that discipline,
the same relationship `security.redaction.PHIRedactionFilter` has to
"pass PHI via extra= fields, never interpolate it": structured practice
first, regex/name-list backstop second, never the other way around.
"""

from __future__ import annotations

from collections.abc import Sequence

from opentelemetry import trace as trace_api
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Tracer
from opentelemetry.util.types import Attributes, AttributeValue

from security.redaction import PHI_FIELD_NAMES, scrub_text

_REDACTED = "[REDACTED]"


def _scrub_attributes(attributes: Attributes) -> dict[str, AttributeValue]:
    scrubbed: dict[str, AttributeValue] = {}
    for key, value in (attributes or {}).items():
        if key in PHI_FIELD_NAMES:
            scrubbed[key] = _REDACTED
        elif isinstance(value, str):
            scrubbed[key] = scrub_text(value)
        else:
            scrubbed[key] = value
    return scrubbed


def _scrub_span(span: ReadableSpan) -> ReadableSpan:
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=_scrub_attributes(span.attributes),
        events=span.events,
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class PHIScrubbingSpanExporter(SpanExporter):
    """Wraps any real `SpanExporter` (decorator pattern, same port/adapter
    shape as every other adapter in this codebase -- `security.kms`,
    `ingestion.virus_scan`, `packets.drafter`) and scrubs span attributes
    before handing spans to it. Never mutates SDK-internal span state --
    each scrubbed span is a fresh `ReadableSpan` built from the original's
    public fields, so this works against any SDK version's export
    pipeline without touching private attributes."""

    def __init__(self, wrapped: SpanExporter) -> None:
        self._wrapped = wrapped

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._wrapped.export([_scrub_span(span) for span in spans])

    def shutdown(self) -> None:
        self._wrapped.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._wrapped.force_flush(timeout_millis)


def setup_tracing(
    exporter: SpanExporter, *, service_name: str = "asc-recovery", set_global: bool = False
) -> Tracer:
    """Wires `exporter` behind `PHIScrubbingSpanExporter` and a
    `TracerProvider`, returning a ready-to-use `Tracer`. Callers supply
    the underlying exporter -- an in-memory one in tests, a real OTLP
    exporter to a real backend once Phase 9 stands up infrastructure.
    That real exporter is not added or tested here, same deferral as
    every other real-cloud-integration adapter in this codebase.

    `set_global=True` additionally registers the constructed provider as
    the process-wide `TracerProvider` (F-09, docs/audit/REGISTER.md) --
    every span this codebase actually creates gets its `Tracer` via
    explicit dependency injection (see `ingestion.pipeline.ingest_file`'s
    `tracer` parameter), never `opentelemetry.trace.get_tracer(__name__)`,
    so nothing here strictly depends on the global registry today. It's
    still real production wiring: any future code, or an
    auto-instrumentation library, that reaches for the ambient global
    tracer instead of explicit DI still finds a working one. Defaults to
    `False` so this function stays safely callable more than once within
    one test session -- the OTel API only honors the *first* ever
    `set_tracer_provider` call in a process and logs a warning on every
    call after that; tests use the returned `Tracer` directly and never
    need the global registry, so they never opt in."""
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(SimpleSpanProcessor(PHIScrubbingSpanExporter(exporter)))
    if set_global:
        trace_api.set_tracer_provider(provider)
    return provider.get_tracer(service_name)
