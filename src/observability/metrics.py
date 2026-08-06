"""OpenTelemetry metrics: business (`dollars_detected`,
`findings_per_remittance`) and system (`ingestion_latency`,
`ingestion_failures`, `llm_cost_per_packet`).

Deliberately does **not** implement `dollars_recovered`, `recovery_rate
by cause`, or `time_to_recovery` -- all three require knowing whether a
payer actually paid a finding, and no outcome-tracking data model exists
anywhere in this codebase. `docs/MASTER-BUILD-PROMPT.md`'s Phase 12
section frames "the outcome feedback loop" as work not yet done. Building
metric instruments around data that doesn't exist would just be
scaffolding around nothing; this is a named, deliberate gap, not an
oversight -- revisit once Phase 12 adds outcome tracking.

`queue_depth` is defined for forward compatibility but always reports 0:
ingestion in this codebase is synchronous, so there is no queue to have a
depth.

Dollar amounts cross into `float` only here, at the telemetry boundary --
OTel's instrument API has no `Decimal` support, and a metrics backend
(Prometheus et al.) stores float64 regardless. This is a deliberate,
narrow exception to CLAUDE.md rule 2: the system of record (`domain`,
`db`) never touches `float` for money; this module only ever receives an
already-correctly-computed `Decimal` and converts it for an approximate,
non-authoritative telemetry signal, never the other way around.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from opentelemetry.metrics import Counter, Histogram, Meter, NoOpMeterProvider, UpDownCounter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader
from opentelemetry.sdk.resources import Resource


@dataclass(frozen=True, slots=True)
class Instruments:
    dollars_detected: Counter
    findings_per_remittance: Histogram
    ingestion_latency_ms: Histogram
    ingestion_failures: Counter
    llm_cost_usd: Histogram
    queue_depth: UpDownCounter


def _build_instruments(meter: Meter) -> Instruments:
    return Instruments(
        dollars_detected=meter.create_counter(
            "dollars_detected",
            unit="USD",
            description="Sum of underpayment shortfall detected across ingested findings",
        ),
        findings_per_remittance=meter.create_histogram(
            "findings_per_remittance",
            unit="1",
            description="Findings produced per ingested remittance file",
        ),
        ingestion_latency_ms=meter.create_histogram(
            "ingestion_latency",
            unit="ms",
            description="Wall-clock time to ingest one remittance file",
        ),
        ingestion_failures=meter.create_counter(
            "ingestion_failures",
            unit="1",
            description="Remittances that ended up quarantined",
        ),
        llm_cost_usd=meter.create_histogram(
            "llm_cost_per_packet",
            unit="USD",
            description="Estimated cost of one recovery-packet LLM draft call",
        ),
        queue_depth=meter.create_up_down_counter(
            "queue_depth",
            unit="1",
            description=(
                "Always 0 today -- ingestion is synchronous, no async queue "
                "exists yet. Defined for forward compatibility."
            ),
        ),
    )


def setup_metrics(reader: MetricReader, *, service_name: str = "asc-recovery") -> Instruments:
    provider = MeterProvider(
        metric_readers=[reader], resource=Resource.create({"service.name": service_name})
    )
    return _build_instruments(provider.get_meter(service_name))


def noop_instruments() -> Instruments:
    """The default for callers (`ingestion.pipeline`, `ingestion.apply`,
    `packets.drafter`) that aren't given real instruments -- e.g. every
    existing test written before Phase 8. Records go nowhere, at
    negligible cost, so instrumentation is additive and never a required
    parameter that breaks callers who don't care about it."""
    return _build_instruments(NoOpMeterProvider().get_meter("asc-recovery"))


def record_ingestion_outcome(
    instruments: Instruments,
    *,
    facility_id: str,
    status: str,
    latency_ms: float,
    dollars_detected: Decimal,
    findings_created: int,
) -> None:
    instruments.ingestion_latency_ms.record(latency_ms, {"status": status})
    if status == "quarantined":
        instruments.ingestion_failures.add(1, {"facility_id": facility_id})
    if dollars_detected > 0:
        instruments.dollars_detected.add(float(dollars_detected), {"facility_id": facility_id})
    instruments.findings_per_remittance.record(findings_created, {"facility_id": facility_id})


def record_llm_usage(
    instruments: Instruments, *, model: str, cost_usd: Decimal
) -> None:
    instruments.llm_cost_usd.record(float(cost_usd), {"model": model})
