from __future__ import annotations

from decimal import Decimal

from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from observability.metrics import record_ingestion_outcome, record_llm_usage, setup_metrics


def _collected(reader: InMemoryMetricReader) -> dict[str, object]:
    data = reader.get_metrics_data()
    metrics: dict[str, object] = {}
    if data is None:
        return metrics
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                metrics[metric.name] = metric.data
    return metrics


def test_ingestion_outcome_records_latency_dollars_and_finding_count() -> None:
    reader = InMemoryMetricReader()
    instruments = setup_metrics(reader)

    record_ingestion_outcome(
        instruments,
        facility_id="tenant-a",
        status="ingested",
        latency_ms=123.4,
        dollars_detected=Decimal("250.50"),
        findings_created=2,
    )

    metrics = _collected(reader)
    latency_points = metrics["ingestion_latency"].data_points  # type: ignore[attr-defined]
    assert latency_points[0].sum == 123.4
    assert latency_points[0].attributes["status"] == "ingested"

    dollars_points = metrics["dollars_detected"].data_points  # type: ignore[attr-defined]
    assert dollars_points[0].value == 250.5

    findings_points = metrics["findings_per_remittance"].data_points  # type: ignore[attr-defined]
    assert findings_points[0].sum == 2

    assert "ingestion_failures" not in metrics


def test_quarantined_ingestion_increments_failure_counter() -> None:
    reader = InMemoryMetricReader()
    instruments = setup_metrics(reader)

    record_ingestion_outcome(
        instruments,
        facility_id="tenant-a",
        status="quarantined",
        latency_ms=10.0,
        dollars_detected=Decimal("0.00"),
        findings_created=0,
    )

    metrics = _collected(reader)
    failure_points = metrics["ingestion_failures"].data_points  # type: ignore[attr-defined]
    assert failure_points[0].value == 1
    # Zero-dollar ingestion (e.g. a quarantined file) doesn't record a
    # spurious dollars_detected data point at all.
    assert "dollars_detected" not in metrics


def test_llm_usage_records_cost_by_model() -> None:
    reader = InMemoryMetricReader()
    instruments = setup_metrics(reader)

    record_llm_usage(instruments, model="claude-sonnet-5", cost_usd=Decimal("0.0234"))

    metrics = _collected(reader)
    cost_points = metrics["llm_cost_per_packet"].data_points  # type: ignore[attr-defined]
    assert cost_points[0].sum == 0.0234
    assert cost_points[0].attributes["model"] == "claude-sonnet-5"


def test_queue_depth_instrument_exists_and_defaults_to_zero() -> None:
    """queue_depth is a documented stub -- ingestion is synchronous, no
    async queue exists yet, but the instrument is defined for forward
    compatibility. Nothing in this codebase increments it today."""
    reader = InMemoryMetricReader()
    instruments = setup_metrics(reader)

    instruments.queue_depth.add(0)

    metrics = _collected(reader)
    depth_points = metrics["queue_depth"].data_points  # type: ignore[attr-defined]
    assert depth_points[0].value == 0
