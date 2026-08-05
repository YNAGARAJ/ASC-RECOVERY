"""Phase 12: `record_finding_outcome`, `list_historical_outcomes`, and
`list_findings_past_deadline_without_outcome` against a real Postgres.

Validation of *when* an outcome may be recorded (already-recorded, or
CORRECT_NO_VARIANCE) is pure domain logic, already covered by
tests/domain/test_outcomes.py -- this file only proves the three
repository functions round-trip correctly through real rows, which the
pure tests can't touch.

Findings here are built directly via `save_findings` rather than through
the full ingestion pipeline (tests/api/test_endpoints_live_db.py's
approach) -- that gives each test exact control over payer/root_cause/
shortfall, which is what these queries actually filter on, without
needing a distinct synthetic 835 file per scenario.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.tenancy import tenant_session
from domain.money import Money
from domain.variance import Finding, RootCause
from tests.ingestion.fixtures import make_contract_version

_PROCEDURE_CODE = "99213"
_RECORDED_BY = "biller@example.com"


def _seed_tenant_with_contract(
    session_factory: sessionmaker[Session], payer_id: str
) -> tuple[uuid.UUID, uuid.UUID]:
    with session_factory() as session, session.begin():
        tenant = repository.create_tenant(session, f"Outcomes test tenant {uuid.uuid4()}")
        tenant_id = tenant.id

    with tenant_session(session_factory, tenant_id) as session:
        contract = repository.create_contract(session, tenant_id, payer_id, "Outcomes contract")
        version = repository.create_contract_version(
            session, tenant_id, contract.id, make_contract_version(payer_id=payer_id)
        )
        version_id = version.id
    return tenant_id, version_id


def _seed_finding(
    session_factory: sessionmaker[Session],
    tenant_id: uuid.UUID,
    contract_version_id: uuid.UUID,
    *,
    root_cause: RootCause = RootCause.MPPR_NOT_APPLIED,
    shortfall: str = "50.00",
) -> uuid.UUID:
    with tenant_session(session_factory, tenant_id) as session:
        remittance, _ = repository.record_remittance_if_new(
            session, tenant_id, uuid.uuid4().hex, source="test", uploaded_by="test-actor"
        )
        claim = repository.create_claim(
            session,
            tenant_id,
            remittance.id,
            patient_control_number=f"PCN-{uuid.uuid4().hex[:8]}",
            payer_claim_control_number=f"PAYERCTRL-{uuid.uuid4().hex[:8]}",
            status="1",
            date_of_service=date(2023, 1, 10),
            total_charge=Decimal("500.00"),
            total_paid_reported=Decimal("400.00"),
            patient_responsibility=Decimal("20.00"),
        )
        line = repository.create_service_line(
            session,
            tenant_id,
            claim.id,
            line_index=0,
            procedure_code=_PROCEDURE_CODE,
            modifiers=(),
            revenue_code=None,
            charge=Decimal("500.00"),
            allowed=Decimal("400.00"),
            paid_computed=Decimal("400.00"),
            service_date=date(2023, 1, 10),
        )
        finding = Finding(
            claim_id=str(claim.id),
            line_index=0,
            procedure_code=_PROCEDURE_CODE,
            expected_allowed=Money("450.00"),
            actual_allowed=Money("400.00"),
            shortfall=Money(shortfall),
            root_cause=root_cause,
            evidence="synthetic test evidence",
        )
        rows = repository.save_findings(
            session, tenant_id, claim.id, {0: line.id}, contract_version_id, [finding]
        )
    return rows[0].id


def test_record_finding_outcome_persists_all_fields(
    app_session_factory: sessionmaker[Session],
) -> None:
    tenant_id, version_id = _seed_tenant_with_contract(app_session_factory, "PAYER-RECORD")
    finding_id = _seed_finding(app_session_factory, tenant_id, version_id)

    with tenant_session(app_session_factory, tenant_id) as session:
        updated = repository.record_finding_outcome(
            session,
            tenant_id,
            finding_id,
            outcome="recovered",
            amount_recovered=Decimal("45.00"),
            recorded_by=_RECORDED_BY,
        )

    assert updated.outcome == "recovered"
    assert updated.amount_recovered == Decimal("45.00")
    assert updated.outcome_recorded_by == _RECORDED_BY
    assert updated.outcome_recorded_at is not None

    with tenant_session(app_session_factory, tenant_id) as session:
        detail = repository.get_finding_detail(session, tenant_id, finding_id)
    assert detail is not None
    assert detail.finding.outcome == "recovered"


def test_list_historical_outcomes_filters_by_payer_root_cause_and_outcome_set(
    app_session_factory: sessionmaker[Session],
) -> None:
    tenant_id, version_id = _seed_tenant_with_contract(app_session_factory, "PAYER-HISTORY")
    other_tenant_id, other_version_id = _seed_tenant_with_contract(
        app_session_factory, "PAYER-HISTORY-OTHER"
    )

    decided_id = _seed_finding(app_session_factory, tenant_id, version_id)
    undecided_id = _seed_finding(app_session_factory, tenant_id, version_id)
    different_root_cause_id = _seed_finding(
        app_session_factory, tenant_id, version_id, root_cause=RootCause.DUPLICATE_LINE
    )
    different_payer_id = _seed_finding(app_session_factory, other_tenant_id, other_version_id)

    with tenant_session(app_session_factory, tenant_id) as session:
        repository.record_finding_outcome(
            session,
            tenant_id,
            decided_id,
            outcome="recovered",
            amount_recovered=Decimal("50.00"),
            recorded_by=_RECORDED_BY,
        )
        repository.record_finding_outcome(
            session,
            tenant_id,
            different_root_cause_id,
            outcome="denied",
            amount_recovered=None,
            recorded_by=_RECORDED_BY,
        )
    with tenant_session(app_session_factory, other_tenant_id) as session:
        repository.record_finding_outcome(
            session,
            other_tenant_id,
            different_payer_id,
            outcome="recovered",
            amount_recovered=Decimal("50.00"),
            recorded_by=_RECORDED_BY,
        )

    with tenant_session(app_session_factory, tenant_id) as session:
        historical = repository.list_historical_outcomes(
            session, tenant_id, "PAYER-HISTORY", RootCause.MPPR_NOT_APPLIED.name
        )

    historical_ids = {row.id for row in historical}
    assert historical_ids == {decided_id}
    assert undecided_id not in historical_ids
    assert different_root_cause_id not in historical_ids
    assert different_payer_id not in historical_ids


def test_list_findings_past_deadline_without_outcome(
    app_session_factory: sessionmaker[Session],
) -> None:
    tenant_id, version_id = _seed_tenant_with_contract(app_session_factory, "PAYER-DEADLINE")
    today = date(2026, 6, 1)
    past_deadline = today - timedelta(days=10)
    future_deadline = today + timedelta(days=10)

    past_no_outcome_id = _seed_finding(app_session_factory, tenant_id, version_id)
    past_with_outcome_id = _seed_finding(app_session_factory, tenant_id, version_id)
    future_deadline_id = _seed_finding(app_session_factory, tenant_id, version_id)
    no_packet_id = _seed_finding(app_session_factory, tenant_id, version_id)
    draft_packet_id = _seed_finding(app_session_factory, tenant_id, version_id)

    with tenant_session(app_session_factory, tenant_id) as session:
        for finding_id, deadline in (
            (past_no_outcome_id, past_deadline),
            (past_with_outcome_id, past_deadline),
            (future_deadline_id, future_deadline),
        ):
            packet = repository.create_recovery_packet(
                session,
                tenant_id,
                finding_id,
                draft_text="synthetic draft",
                deadline=deadline,
                generated_by=_RECORDED_BY,
            )
            repository.decide_recovery_packet(
                session, tenant_id, packet.id, approve=True, decided_by=_RECORDED_BY
            )
        draft_packet = repository.create_recovery_packet(
            session,
            tenant_id,
            draft_packet_id,
            draft_text="synthetic draft",
            deadline=past_deadline,
            generated_by=_RECORDED_BY,
        )
        # Left in "draft" -- never approved, so it must not surface below.
        assert draft_packet.status == "draft"

        repository.record_finding_outcome(
            session,
            tenant_id,
            past_with_outcome_id,
            outcome="recovered",
            amount_recovered=Decimal("50.00"),
            recorded_by=_RECORDED_BY,
        )

    with tenant_session(app_session_factory, tenant_id) as session:
        overdue = repository.list_findings_past_deadline_without_outcome(session, tenant_id, today)

    overdue_ids = {row.id for row in overdue}
    assert overdue_ids == {past_no_outcome_id}
    assert past_with_outcome_id not in overdue_ids
    assert future_deadline_id not in overdue_ids
    assert no_packet_id not in overdue_ids
    assert draft_packet_id not in overdue_ids
