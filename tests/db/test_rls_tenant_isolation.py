"""The Phase 3 hard gate: an unfiltered read as tenant A must not return
tenant B's claims, with Row-Level Security -- not application code -- as
the only thing stopping it.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.tenancy import tenant_session


def _seed_tenant_with_claim(
    session_factory: sessionmaker[Session], patient_control_number: str
) -> uuid.UUID:
    with session_factory() as session, session.begin():
        tenant = repository.create_tenant(session, f"RLS test tenant {uuid.uuid4()}")
        tenant_id = tenant.id

    with tenant_session(session_factory, tenant_id) as session:
        remittance, _ = repository.record_remittance_if_new(
            session, tenant_id, uuid.uuid4().hex, source="upload", uploaded_by="tester"
        )
        repository.create_claim(
            session,
            tenant_id,
            remittance.id,
            patient_control_number=patient_control_number,
            payer_claim_control_number="PAYERCTRL1",
            status="1",
            date_of_service=date(2023, 1, 10),
            total_charge=Decimal("500.00"),
            total_paid_reported=Decimal("430.00"),
            patient_responsibility=Decimal("20.00"),
        )
    return tenant_id


def test_rls_blocks_cross_tenant_read_with_app_filtering_disabled(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    tenant_a = _seed_tenant_with_claim(app_session_factory, "TENANT-A-CLAIM")
    tenant_b = _seed_tenant_with_claim(app_session_factory, "TENANT-B-CLAIM")

    # As asc_app, scoped to tenant A, with NO `WHERE tenant_id = ...` at
    # all -- app-level filtering deliberately absent. RLS is the only thing
    # that can stop tenant B's row from coming back.
    with tenant_session(app_session_factory, tenant_a) as session:
        rows = session.execute(text("SELECT patient_control_number FROM claims")).scalars().all()
    seen = set(rows)
    assert "TENANT-A-CLAIM" in seen
    assert "TENANT-B-CLAIM" not in seen

    # Prove it's RLS, not missing data or a fluke: with RLS forced off, the
    # identical query (scoped by id, not tenant, to avoid cross-test noise)
    # returns both tenants' claims.
    with owner_engine.connect() as conn:
        conn.execute(text("ALTER TABLE claims NO FORCE ROW LEVEL SECURITY"))
        conn.commit()
        try:
            unforced = (
                conn.execute(
                    text(
                        "SELECT patient_control_number FROM claims "
                        "WHERE tenant_id IN (:a, :b)"
                    ),
                    {"a": str(tenant_a), "b": str(tenant_b)},
                )
                .scalars()
                .all()
            )
        finally:
            conn.execute(text("ALTER TABLE claims FORCE ROW LEVEL SECURITY"))
            conn.commit()
    assert set(unforced) == {"TENANT-A-CLAIM", "TENANT-B-CLAIM"}


def test_rls_blocks_cross_tenant_read_even_by_known_id(
    app_session_factory: sessionmaker[Session],
) -> None:
    """IDOR-style check: knowing another tenant's row id (e.g. a guessed or
    leaked UUID) must not be enough to read it -- RLS applies regardless of
    how precisely the row is targeted, not just to unfiltered scans."""
    tenant_a = _seed_tenant_with_claim(app_session_factory, "IDOR-TENANT-A")
    tenant_b = _seed_tenant_with_claim(app_session_factory, "IDOR-TENANT-B")

    with tenant_session(app_session_factory, tenant_b) as session:
        rows = session.execute(text("SELECT id FROM claims")).scalars().all()
    assert len(rows) >= 1

    with tenant_session(app_session_factory, tenant_a) as session:
        cross_tenant_rows = (
            session.execute(text("SELECT id FROM claims WHERE id = :id"), {"id": str(rows[0])})
            .scalars()
            .all()
        )
    assert cross_tenant_rows == []
