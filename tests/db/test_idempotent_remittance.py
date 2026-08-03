"""Re-ingesting an identical remittance file (same tenant, same content
hash) must create zero new claims -- the point of `UNIQUE (tenant_id,
file_hash)` plus `repository.record_remittance_if_new`'s
`ON CONFLICT DO NOTHING`.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.models import Claim as ClaimModel
from db.tenancy import tenant_session


def _new_tenant(session_factory: sessionmaker[Session], label: str) -> uuid.UUID:
    with session_factory() as session, session.begin():
        tenant = repository.create_tenant(session, f"{label} {uuid.uuid4()}")
        return tenant.id


def test_reingesting_identical_file_hash_creates_zero_new_claims(
    app_session_factory: sessionmaker[Session],
) -> None:
    tenant_id = _new_tenant(app_session_factory, "Idempotency test tenant")
    file_hash = uuid.uuid4().hex

    def ingest_once() -> None:
        with tenant_session(app_session_factory, tenant_id) as session:
            remittance, is_new = repository.record_remittance_if_new(
                session, tenant_id, file_hash, source="upload", uploaded_by="tester"
            )
            if not is_new:
                return
            repository.create_claim(
                session,
                tenant_id,
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

    with tenant_session(app_session_factory, tenant_id) as session:
        claim_count = session.execute(
            select(func.count())
            .select_from(ClaimModel)
            .where(ClaimModel.tenant_id == tenant_id)
        ).scalar_one()

    assert claim_count == 1


def test_record_remittance_if_new_reports_is_new_correctly(
    app_session_factory: sessionmaker[Session],
) -> None:
    tenant_id = _new_tenant(app_session_factory, "Idempotency flag test tenant")
    file_hash = uuid.uuid4().hex

    with tenant_session(app_session_factory, tenant_id) as session:
        _, first_is_new = repository.record_remittance_if_new(
            session, tenant_id, file_hash, source="upload", uploaded_by="tester"
        )
    with tenant_session(app_session_factory, tenant_id) as session:
        _, second_is_new = repository.record_remittance_if_new(
            session, tenant_id, file_hash, source="upload", uploaded_by="tester"
        )

    assert first_is_new is True
    assert second_is_new is False


def test_different_tenants_can_reuse_the_same_file_hash(
    app_session_factory: sessionmaker[Session],
) -> None:
    """The uniqueness constraint is (tenant_id, file_hash), not file_hash
    alone -- two different ASCs could plausibly receive byte-identical 835
    files from the same payer on the same day."""
    tenant_a = _new_tenant(app_session_factory, "Shared-hash tenant A")
    tenant_b = _new_tenant(app_session_factory, "Shared-hash tenant B")
    file_hash = uuid.uuid4().hex

    with tenant_session(app_session_factory, tenant_a) as session:
        _, a_is_new = repository.record_remittance_if_new(
            session, tenant_a, file_hash, source="upload", uploaded_by="tester"
        )
    with tenant_session(app_session_factory, tenant_b) as session:
        _, b_is_new = repository.record_remittance_if_new(
            session, tenant_b, file_hash, source="upload", uploaded_by="tester"
        )

    assert a_is_new is True
    assert b_is_new is True
