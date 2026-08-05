"""In-memory `Repository` fake -- tenant-partitioned storage implementing
the same `api.repository.Repository` Protocol `PostgresRepository` does.
This is what lets the full role x endpoint x tenant authorization matrix
run as real, passing tests without a live Postgres (the same trick
`ingestion.plan`/`apply` used in Phase 5).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal

from api.repository import (
    AccessEventSummary,
    AuditLogEntry,
    AuditLogFilters,
    ContractSummary,
    ContractVersionInput,
    FindingDetail,
    FindingFilters,
    FindingSummary,
    Page,
    PagedResult,
    RecoveryPacketSummary,
    UserRecord,
)
from domain.deadlines import calculate_appeal_deadline
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import DuplicateOutcome
from ingestion.virus_scan import VirusScanner

_DEFAULT_TIMELY_FILING_DAYS = 90


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class FakeRepository:
    users: dict[str, UserRecord] = field(default_factory=dict)
    findings: dict[uuid.UUID, tuple[uuid.UUID, FindingDetail]] = field(default_factory=dict)
    contracts: dict[uuid.UUID, tuple[uuid.UUID, ContractSummary]] = field(default_factory=dict)
    audit_entries: dict[uuid.UUID, tuple[uuid.UUID, AuditLogEntry]] = field(default_factory=dict)
    packets: dict[uuid.UUID, tuple[uuid.UUID, RecoveryPacketSummary]] = field(default_factory=dict)
    access_events: list[tuple[uuid.UUID, AccessEventSummary]] = field(default_factory=list)
    ingest_calls: list[tuple[uuid.UUID, bytes, str, str]] = field(default_factory=list)
    next_ingest_outcome: IngestionOutcome | DuplicateOutcome | None = None
    healthy: bool = True

    # --- seeding helpers, test-only -------------------------------------

    def seed_user(self, subject: str, *, tenant_id: uuid.UUID, role: str) -> None:
        self.users[subject] = UserRecord(tenant_id=tenant_id, role=role, subject=subject)

    def seed_finding(self, tenant_id: uuid.UUID, detail: FindingDetail) -> uuid.UUID:
        self.findings[detail.summary.id] = (tenant_id, detail)
        return detail.summary.id

    def seed_contract(self, tenant_id: uuid.UUID, summary: ContractSummary) -> None:
        self.contracts[summary.id] = (tenant_id, summary)

    def seed_audit_entry(self, tenant_id: uuid.UUID, entry: AuditLogEntry) -> None:
        self.audit_entries[entry.id] = (tenant_id, entry)

    # --- Repository protocol ---------------------------------------------

    def ping(self) -> bool:
        return self.healthy

    def get_user_by_subject(self, subject: str) -> UserRecord | None:
        return self.users.get(subject)

    def ingest_remittance(
        self,
        tenant_id: uuid.UUID,
        *,
        content: bytes,
        source: str,
        uploaded_by: str,
        scanner: VirusScanner,
    ) -> IngestionOutcome | DuplicateOutcome:
        self.ingest_calls.append((tenant_id, content, source, uploaded_by))
        scan_result = scanner.scan(content)
        if not scan_result.clean:
            return IngestionOutcome(
                remittance_id=uuid.uuid4(),
                status="quarantined",
                claims_created=0,
                findings_created=0,
                reconciliation_mismatches=0,
                dollars_detected=Decimal("0"),
            )
        if self.next_ingest_outcome is not None:
            return self.next_ingest_outcome
        return IngestionOutcome(
            remittance_id=uuid.uuid4(),
            status="ingested",
            claims_created=1,
            findings_created=1,
            reconciliation_mismatches=0,
            dollars_detected=Decimal("50.00"),
        )

    def list_findings(
        self, tenant_id: uuid.UUID, *, filters: FindingFilters, page: Page
    ) -> PagedResult[FindingSummary]:
        items = [
            detail.summary
            for tid, detail in self.findings.values()
            if tid == tenant_id
        ]
        if filters.root_cause is not None:
            items = [i for i in items if i.root_cause == filters.root_cause]
        if filters.claim_id is not None:
            items = [i for i in items if i.claim_id == filters.claim_id]
        if filters.min_shortfall is not None:
            items = [i for i in items if float(i.shortfall) >= float(filters.min_shortfall)]
        total = len(items)
        page_items = items[page.offset : page.offset + page.limit]
        return PagedResult(items=page_items, total=total, limit=page.limit, offset=page.offset)

    def get_finding_detail(
        self, tenant_id: uuid.UUID, finding_id: uuid.UUID, *, actor: str
    ) -> FindingDetail | None:
        entry = self.findings.get(finding_id)
        if entry is None:
            return None
        tid, detail = entry
        if tid != tenant_id:
            # Cross-tenant lookup by known id must miss, not 403 -- same
            # IDOR-shaped guarantee tests/db/test_rls_tenant_isolation.py
            # proves at the DB layer.
            return None
        self._record_access(
            tenant_id,
            actor=actor,
            action="finding_detail_view",
            claim_id=detail.summary.claim_id,
            purpose="finding_detail_view",
        )
        return detail

    def _record_access(
        self, tenant_id: uuid.UUID, *, actor: str, action: str, claim_id: uuid.UUID, purpose: str
    ) -> None:
        event = AccessEventSummary(
            occurred_at=now(),
            actor=actor,
            action=action,
            resource_type="claim",
            resource_id=str(claim_id),
            purpose=purpose,
        )
        self.access_events.append((tenant_id, event))

    def list_contracts(self, tenant_id: uuid.UUID, *, page: Page) -> PagedResult[ContractSummary]:
        items = [summary for tid, summary in self.contracts.values() if tid == tenant_id]
        total = len(items)
        page_items = items[page.offset : page.offset + page.limit]
        return PagedResult(items=page_items, total=total, limit=page.limit, offset=page.offset)

    def create_contract(
        self, tenant_id: uuid.UUID, *, payer_id: str, name: str
    ) -> ContractSummary:
        summary = ContractSummary(id=uuid.uuid4(), payer_id=payer_id, name=name, created_at=now())
        self.contracts[summary.id] = (tenant_id, summary)
        return summary

    def create_contract_version(
        self, tenant_id: uuid.UUID, contract_id: uuid.UUID, data: ContractVersionInput
    ) -> uuid.UUID:
        return uuid.uuid4()

    def list_audit_log(
        self, tenant_id: uuid.UUID, *, filters: AuditLogFilters, page: Page
    ) -> PagedResult[AuditLogEntry]:
        items = [entry for tid, entry in self.audit_entries.values() if tid == tenant_id]
        total = len(items)
        page_items = items[page.offset : page.offset + page.limit]
        return PagedResult(items=page_items, total=total, limit=page.limit, offset=page.offset)

    def write_audit_log(
        self,
        tenant_id: uuid.UUID,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        phi_accessed: bool,
        request_id: str | None,
    ) -> None:
        entry = AuditLogEntry(
            id=uuid.uuid4(),
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            occurred_at=now(),
            source_ip=None,
            phi_accessed=phi_accessed,
            request_id=request_id,
        )
        self.audit_entries[entry.id] = (tenant_id, entry)

    def generate_packet(
        self, tenant_id: uuid.UUID, finding_id: uuid.UUID, *, generated_by: str
    ) -> RecoveryPacketSummary | None:
        """Only proves tenant-scoping/state-machine plumbing through the
        API layer -- the actual currency/PHI-safety logic this stands in
        for is proven directly against `packets.service` in
        tests/packets/, not re-tested redundantly here."""
        entry = self.findings.get(finding_id)
        if entry is None or entry[0] != tenant_id:
            return None
        _, detail = entry
        self._record_access(
            tenant_id,
            actor=generated_by,
            action="packet_generation",
            claim_id=detail.summary.claim_id,
            purpose="packet_generation",
        )
        packet = RecoveryPacketSummary(
            id=uuid.uuid4(),
            finding_id=finding_id,
            status="draft",
            draft_text=f"Draft appeal letter for finding {finding_id}.",
            deadline=calculate_appeal_deadline(now().date(), _DEFAULT_TIMELY_FILING_DAYS),
            generated_by=generated_by,
            generated_at=now(),
            decided_by=None,
            decided_at=None,
        )
        self.packets[packet.id] = (tenant_id, packet)
        return packet

    def list_packets(
        self, tenant_id: uuid.UUID, finding_id: uuid.UUID, *, actor: str
    ) -> list[RecoveryPacketSummary]:
        entry = self.findings.get(finding_id)
        if entry is not None and entry[0] == tenant_id:
            self._record_access(
                tenant_id,
                actor=actor,
                action="packet_list_view",
                claim_id=entry[1].summary.claim_id,
                purpose="packet_list_view",
            )
        return [
            packet
            for tid, packet in self.packets.values()
            if tid == tenant_id and packet.finding_id == finding_id
        ]

    def get_claim_access_history(
        self, tenant_id: uuid.UUID, claim_id: uuid.UUID
    ) -> tuple[AccessEventSummary, ...]:
        events = [
            event
            for tid, event in self.access_events
            if tid == tenant_id and event.resource_id == str(claim_id)
        ]
        return tuple(sorted(events, key=lambda event: event.occurred_at))

    def decide_packet(
        self, tenant_id: uuid.UUID, packet_id: uuid.UUID, *, approve: bool, decided_by: str
    ) -> RecoveryPacketSummary | None:
        entry = self.packets.get(packet_id)
        if entry is None or entry[0] != tenant_id:
            return None
        _, packet = entry
        updated = replace(
            packet,
            status="approved" if approve else "rejected",
            decided_by=decided_by,
            decided_at=now(),
        )
        self.packets[packet_id] = (tenant_id, updated)
        return updated
