"""Proves `ingestion.pipeline.ingest_file`'s tracing/metrics wrapper
actually emits a real span and real metric data points when given real
instrumentation -- the existing idempotency/quarantine/audit tests in
this directory all use the no-op defaults, so none of them exercise this
wiring. Requires a live Postgres -- see tests/ingestion/conftest.py.
"""

from __future__ import annotations

from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.orm import Session, sessionmaker

from db.tenancy import tenant_session
from domain.money import Money
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import ingest_file
from ingestion.virus_scan import EicarAwareScanner
from observability.metrics import setup_metrics
from observability.tracing import setup_tracing
from tests.domain.fixtures_x835 import minimal_valid_835
from tests.ingestion.conftest import make_test_encryptor, seed_tenant_with_contract


def test_ingest_file_emits_a_scrubbed_span_and_real_metrics(
    app_session_factory: sessionmaker[Session],
) -> None:
    # minimal_valid_835() reports allowed=450 for 99213 -- a contract
    # price below that (the shared default, make_contract_version()'s
    # $100) prices as an apparent *overpayment* (negative shortfall),
    # which record_ingestion_outcome deliberately never reports as
    # dollars_detected (an OTel Counter can't accept a negative value).
    # This test needs a genuine positive shortfall to prove the
    # instrument actually fires, so it prices 99213 above what's
    # reported allowed instead of using the shared default.
    tenant_id = seed_tenant_with_contract(
        app_session_factory,
        "Observability tenant",
        fee_schedule={"99213": Money("500.00")},
    )
    span_exporter = InMemorySpanExporter()
    tracer = setup_tracing(span_exporter)
    metric_reader = InMemoryMetricReader()
    instruments = setup_metrics(metric_reader)

    with tenant_session(app_session_factory, tenant_id) as session:
        outcome = ingest_file(
            session,
            tenant_id,
            content=minimal_valid_835().encode("utf-8"),
            source="upload",
            uploaded_by="observability-tester",
            scanner=EicarAwareScanner(),
            encryptor=make_test_encryptor(),
            tracer=tracer,
            instruments=instruments,
        )

    assert isinstance(outcome, IngestionOutcome)
    assert outcome.status == "ingested"

    (span,) = span_exporter.get_finished_spans()
    assert span.name == "ingestion.ingest_file"
    assert span.attributes is not None
    assert span.attributes["tenant_id"] == str(tenant_id)
    assert span.attributes["outcome_status"] == "ingested"

    metrics_data = metric_reader.get_metrics_data()
    assert metrics_data is not None
    metric_names = {
        metric.name
        for resource_metrics in metrics_data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }
    assert "ingestion_latency" in metric_names
    assert "dollars_detected" in metric_names
    assert "findings_per_remittance" in metric_names
