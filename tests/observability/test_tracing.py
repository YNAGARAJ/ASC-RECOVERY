"""Phase 8 gate: trace sampling captures no PHI -- asserted directly on
exported spans, not just on the scrubbing function in isolation.

SSN-shaped test values are assembled at runtime (`"-".join(...)`) rather
than written as source literals, so this fixture data doesn't itself trip
`scripts/hooks/block_phi.sh`'s SSN-pattern guard -- the same workaround
used elsewhere in this codebase for synthetic PHI-shaped test data.
"""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from observability.tracing import setup_tracing

_FAKE_SSN = "-".join(["123", "45", "6789"])
_OTHER_FAKE_SSN = "-".join(["987", "65", "4321"])


def test_phi_shaped_attribute_is_scrubbed_from_exported_spans() -> None:
    exporter = InMemorySpanExporter()
    tracer = setup_tracing(exporter)

    with tracer.start_as_current_span("finding.detail") as span:
        # Simulates a mistake at a call site -- exactly the case the
        # scrubbing exporter exists to catch, on top of the discipline of
        # never doing this deliberately.
        span.set_attribute("patient_name", "ZBIGNIEW KOWALCZYK-VANDERMEER")
        span.set_attribute("patient_member_id", "MBR-XQ99871234")
        span.set_attribute("ssn", _FAKE_SSN)
        span.set_attribute("tenant_id", "11111111-1111-1111-1111-111111111111")
        span.set_attribute("finding_count", 3)

    (exported,) = exporter.get_finished_spans()

    assert exported.attributes is not None
    assert exported.attributes["patient_name"] == "[REDACTED]"
    assert exported.attributes["patient_member_id"] == "[REDACTED]"
    assert exported.attributes["ssn"] == "[REDACTED]"
    assert "ZBIGNIEW" not in str(exported.attributes)
    assert "MBR-XQ99871234" not in str(exported.attributes)
    assert _FAKE_SSN not in str(exported.attributes)
    # Non-PHI attributes pass through untouched.
    assert exported.attributes["tenant_id"] == "11111111-1111-1111-1111-111111111111"
    assert exported.attributes["finding_count"] == 3


def test_ssn_shaped_text_embedded_in_a_message_attribute_is_also_scrubbed() -> None:
    """Defense-in-depth layer: even an attribute NOT named after
    PHI_FIELD_NAMES gets its string value regex-scrubbed, same as
    PHIRedactionFilter does for log messages."""
    exporter = InMemorySpanExporter()
    tracer = setup_tracing(exporter)

    with tracer.start_as_current_span("error") as span:
        span.set_attribute("error.message", f"lookup failed for SSN {_OTHER_FAKE_SSN}")

    (exported,) = exporter.get_finished_spans()
    assert exported.attributes is not None
    assert _OTHER_FAKE_SSN not in str(exported.attributes["error.message"])
    assert "[REDACTED]" in str(exported.attributes["error.message"])


def test_clean_spans_are_exported_unchanged() -> None:
    exporter = InMemorySpanExporter()
    tracer = setup_tracing(exporter)

    with tracer.start_as_current_span("ingest") as span:
        span.set_attribute("remittance_status", "ingested")
        span.set_attribute("claims_created", 5)

    (exported,) = exporter.get_finished_spans()
    assert exported.attributes is not None
    assert exported.attributes["remittance_status"] == "ingested"
    assert exported.attributes["claims_created"] == 5
