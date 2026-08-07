"""Phase 7's `on_progress`/`should_cancel` callbacks on
`apply_ingestion_plan` -- checked every `_CALLBACK_INTERVAL` (25) claims,
not every single one, and a `should_cancel() -> True` mid-file raises
`JobCancelledError` before any further DB writes happen. Requires a live
Postgres -- see tests/ingestion/conftest.py. Builds a `FileIngestionPlan`
directly (bypassing X12 parsing, already covered by tests/ingestion/
test_plan.py) with `contract_version=None`/`findings=()` on every claim so
no fee-schedule contract needs seeding -- this file is only exercising
apply.py's own claim-count bookkeeping, not pricing.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from db.base import make_session_factory
from db.models import Claim as ClaimModel
from domain.money import Money
from domain.x835 import Claim835, ClaimStatus, Entity, ServiceLine835
from ingestion.apply import JobCancelledError, apply_ingestion_plan
from ingestion.plan import ClaimIngestionPlan, FileIngestionPlan, TransactionIngestionPlan
from ingestion.reconcile import ReconciliationResult
from tests.db.conftest import seed_org_facility_user
from tests.ingestion.conftest import make_test_encryptor

_CALLBACK_INTERVAL = 25  # mirrors ingestion/apply.py's own, private constant


def _claim_plan(index: int) -> ClaimIngestionPlan:
    line = ServiceLine835(
        procedure_code="99213",
        modifiers=(),
        revenue_code=None,
        charge=Money("100.00"),
        paid_reported=Money("80.00"),
        units=Decimal("1"),
        service_date=date(2023, 1, 10),
        adjustments=(),
        remarks=(),
        allowed=Money("80.00"),
        paid_computed=Money("80.00"),
        raw_elements=(),
    )
    claim = Claim835(
        patient_control_number=f"BULK{index:04d}",
        status=ClaimStatus.PRIMARY,
        total_charge=Money("100.00"),
        total_paid_reported=Money("80.00"),
        patient_responsibility=Money("20.00"),
        claim_filing_indicator_code="12",
        payer_claim_control_number=f"BULKCTRL{index:04d}",
        patient=Entity(
            entity_identifier_code="QC",
            name="Test Patient",
            id_qualifier="MI",
            id_code=f"MEMBER{index:04d}",
        ),
        rendering_provider=None,
        dates=(),
        adjustments=(),
        mia=None,
        moa=None,
        remarks=(),
        service_lines=(line,),
        total_allowed=Money("80.00"),
        total_paid_computed=Money("80.00"),
        is_reconciled=True,
        reconciliation_diff=Money.zero(),
    )
    return ClaimIngestionPlan(
        claim=claim,
        date_of_service=date(2023, 1, 10),
        contract_version=None,
        findings=(),
        is_reversal=False,
        skip_reason=None,
    )


def _make_plan(claim_count: int) -> FileIngestionPlan:
    reconciliation = ReconciliationResult(
        matches=True, bpr_total=Money.zero(), computed_total=Money.zero(), difference=Money.zero()
    )
    claims = tuple(_claim_plan(i) for i in range(claim_count))
    return FileIngestionPlan(
        quarantine_reason=None,
        transactions=(TransactionIngestionPlan(reconciliation=reconciliation, claims=claims),),
        parse_errors=(),
    )


def _new_remittance(owner_engine: Engine, user_id: uuid.UUID, facility_id: uuid.UUID) -> uuid.UUID:
    session_factory = make_session_factory(owner_engine)
    with access_session(session_factory, user_id) as session:
        row, _is_new = repository.record_remittance_if_new(
            session, facility_id, uuid.uuid4().hex, source="upload", uploaded_by="tester"
        )
        return row.id


def test_on_progress_and_should_cancel_fire_every_callback_interval_not_every_claim(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, "Progress callback tenant")
    remittance_id = _new_remittance(owner_engine, user_id, facility_id)
    claim_count = _CALLBACK_INTERVAL + 5  # one mid-file checkpoint, one final report
    plan = _make_plan(claim_count)

    progress_calls: list[tuple[int, int]] = []
    cancel_calls = 0

    def on_progress(processed: int, total: int) -> None:
        progress_calls.append((processed, total))

    def should_cancel() -> bool:
        nonlocal cancel_calls
        cancel_calls += 1
        return False

    with access_session(app_session_factory, user_id) as session:
        outcome = apply_ingestion_plan(
            session,
            facility_id,
            plan,
            remittance_id=remittance_id,
            actor="tester",
            contract_version_ids={},
            encryptor=make_test_encryptor(),
            on_progress=on_progress,
            should_cancel=should_cancel,
        )

    assert outcome.claims_created == claim_count
    # Checked at claims_created == 0 (before the first claim) and again at
    # claims_created == _CALLBACK_INTERVAL -- never on every claim.
    assert cancel_calls == 2
    # One mid-file checkpoint (25/30), one unconditional final report (30/30).
    assert progress_calls == [(_CALLBACK_INTERVAL, claim_count), (claim_count, claim_count)]


def test_should_cancel_true_raises_before_any_claim_is_persisted(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, "Cancel callback tenant")
    remittance_id = _new_remittance(owner_engine, user_id, facility_id)
    plan = _make_plan(3)

    with pytest.raises(JobCancelledError):
        with access_session(app_session_factory, user_id) as session:
            apply_ingestion_plan(
                session,
                facility_id,
                plan,
                remittance_id=remittance_id,
                actor="tester",
                contract_version_ids={},
                encryptor=make_test_encryptor(),
                should_cancel=lambda: True,
            )

    # The raise propagated out of access_session's `with` block uncaught,
    # which rolled back the whole transaction -- a cancelled job leaves no
    # partial claims behind, same "clean slate" property this module's own
    # docstring promises.
    with access_session(app_session_factory, user_id) as session:
        claim_count = session.execute(
            select(func.count())
            .select_from(ClaimModel)
            .where(ClaimModel.facility_id == facility_id)
        ).scalar_one()
    assert claim_count == 0
