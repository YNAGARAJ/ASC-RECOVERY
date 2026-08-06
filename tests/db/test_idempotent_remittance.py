"""Re-ingesting an identical remittance file (same facility, same content
hash) must create zero new claims -- the point of `UNIQUE (facility_id,
file_hash)` plus `repository.record_remittance_if_new`'s
`ON CONFLICT DO NOTHING`.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from db.models import Claim as ClaimModel
from tests.db.conftest import seed_org_facility_user


def _new_facility(owner_engine: Engine, label: str) -> tuple[uuid.UUID, uuid.UUID]:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, label)
    return user_id, facility_id


def test_reingesting_identical_file_hash_creates_zero_new_claims(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = _new_facility(owner_engine, "Idempotency test tenant")
    file_hash = uuid.uuid4().hex

    def ingest_once() -> None:
        with access_session(app_session_factory, user_id) as session:
            remittance, is_new = repository.record_remittance_if_new(
                session, facility_id, file_hash, source="upload", uploaded_by="tester"
            )
            if not is_new:
                return
            repository.create_claim(
                session,
                facility_id,
                remittance.id,
                patient_control_number="IDEMPOTENT-CLAIM",
                payer_claim_control_number="PAYERCTRL1",
                status="1",
                date_of_service=date(2023, 1, 10),
                total_charge=Decimal("500.00"),
                total_paid_reported=Decimal("430.00"),
                patient_responsibility=Decimal("20.00"),
            )

    ingest_once()
    ingest_once()
    ingest_once()

    with access_session(app_session_factory, user_id) as session:
        claim_count = session.execute(
            select(func.count())
            .select_from(ClaimModel)
            .where(ClaimModel.facility_id == facility_id)
        ).scalar_one()

    assert claim_count == 1


def test_record_remittance_if_new_reports_is_new_correctly(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = _new_facility(owner_engine, "Idempotency flag test tenant")
    file_hash = uuid.uuid4().hex

    with access_session(app_session_factory, user_id) as session:
        _, first_is_new = repository.record_remittance_if_new(
            session, facility_id, file_hash, source="upload", uploaded_by="tester"
        )
    with access_session(app_session_factory, user_id) as session:
        _, second_is_new = repository.record_remittance_if_new(
            session, facility_id, file_hash, source="upload", uploaded_by="tester"
        )

    assert first_is_new is True
    assert second_is_new is False


def test_different_facilities_can_reuse_the_same_file_hash(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    """The uniqueness constraint is (facility_id, file_hash), not
    file_hash alone -- two different ASCs could plausibly receive
    byte-identical 835 files from the same payer on the same day."""
    user_a, facility_a = _new_facility(owner_engine, "Shared-hash tenant A")
    user_b, facility_b = _new_facility(owner_engine, "Shared-hash tenant B")
    file_hash = uuid.uuid4().hex

    with access_session(app_session_factory, user_a) as session:
        _, a_is_new = repository.record_remittance_if_new(
            session, facility_a, file_hash, source="upload", uploaded_by="tester"
        )
    with access_session(app_session_factory, user_b) as session:
        _, b_is_new = repository.record_remittance_if_new(
            session, facility_b, file_hash, source="upload", uploaded_by="tester"
        )

    assert a_is_new is True
    assert b_is_new is True
