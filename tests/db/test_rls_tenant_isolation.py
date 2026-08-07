"""Phase 4's hard gate (`docs/MASTER-BUILD-PROMPT-V2.md`): resolved
facility/org access -- not application code -- is what blocks a
cross-facility read, plus the phase's other four required proofs: a
billing-company user scoped to specific facilities cannot read a third,
a parent-org user reaches descendant-org facilities, revoking a
membership takes effect immediately, and a five-level hierarchy resolves
(and terminates) correctly. Field-level PHI masking for the `analyst`
role is proven end to end here too (against a real decrypted claim),
complementing the pure-function proof in
`tests/security/test_phi_masking.py`.

This is the direct descendant of the pre-Phase-4 flat-tenant version of
this file -- same "RLS forced off proves it was RLS, not missing data"
technique, same IDOR-by-known-id shape, now against
`resolve_accessible_facility_ids`/`resolve_accessible_org_ids`
(`alembic/versions/0001_initial_schema.py`) instead of a flat
`tenant_id = current_setting(...)` equality check.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from api.repository import PostgresRepository
from db import repository
from db.access import access_session
from db.base import make_session_factory
from domain.money import Money
from domain.variance import Finding, RootCause
from packets.drafter import ScriptedPacketDrafter
from security.encryption import EnvelopeEncryptor
from security.kms_local import LocalKMS
from security.phi_columns import encrypt_phi_field
from security.rbac import Role
from tests.db.conftest import seed_org_facility_user

_PROCEDURE_CODE = "99213"


def _make_test_encryptor() -> EnvelopeEncryptor:
    kms = LocalKMS()
    kms.generate_kek("rls-test-kek")
    return EnvelopeEncryptor(kms)


def _seed_claim(
    session_factory: sessionmaker[Session],
    user_id: uuid.UUID,
    facility_id: uuid.UUID,
    patient_control_number: str,
    *,
    patient_name_encrypted: str | None = None,
    patient_member_id_encrypted: str | None = None,
) -> uuid.UUID:
    with access_session(session_factory, user_id) as session:
        remittance, _ = repository.record_remittance_if_new(
            session, facility_id, uuid.uuid4().hex, source="upload", uploaded_by="tester"
        )
        claim = repository.create_claim(
            session,
            facility_id,
            remittance.id,
            patient_control_number=patient_control_number,
            payer_claim_control_number="PAYERCTRL1",
            status="1",
            date_of_service=date(2023, 1, 10),
            total_charge=Decimal("500.00"),
            total_paid_reported=Decimal("430.00"),
            patient_responsibility=Decimal("20.00"),
            patient_name_encrypted=patient_name_encrypted,
            patient_member_id_encrypted=patient_member_id_encrypted,
        )
        return claim.id


def _seed_finding_for_claim(
    session_factory: sessionmaker[Session],
    user_id: uuid.UUID,
    facility_id: uuid.UUID,
    claim_id: uuid.UUID,
) -> uuid.UUID:
    with access_session(session_factory, user_id) as session:
        line = repository.create_service_line(
            session,
            facility_id,
            claim_id,
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
            claim_id=str(claim_id),
            line_index=0,
            procedure_code=_PROCEDURE_CODE,
            expected_allowed=Money("450.00"),
            actual_allowed=Money("400.00"),
            shortfall=Money("50.00"),
            root_cause=RootCause.MPPR_NOT_APPLIED,
            evidence="synthetic test evidence",
        )
        rows = repository.save_findings(
            session, facility_id, claim_id, {0: line.id}, None, [finding]
        )
        return rows[0].id


# --- Requirement 1: cross-facility read blocked at the database ---------------


def test_rls_blocks_cross_facility_read_with_app_filtering_disabled(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    user_a, _org_a, facility_a = seed_org_facility_user(owner_engine, "RLS test tenant A")
    user_b, _org_b, facility_b = seed_org_facility_user(owner_engine, "RLS test tenant B")
    _seed_claim(app_session_factory, user_a, facility_a, "TENANT-A-CLAIM")
    _seed_claim(app_session_factory, user_b, facility_b, "TENANT-B-CLAIM")

    # As asc_app, scoped to user A, with NO `WHERE facility_id = ...` at
    # all -- app-level filtering deliberately absent. RLS is the only thing
    # that can stop facility B's row from coming back.
    with access_session(app_session_factory, user_a) as session:
        rows = session.execute(text("SELECT patient_control_number FROM claims")).scalars().all()
    seen = set(rows)
    assert "TENANT-A-CLAIM" in seen
    assert "TENANT-B-CLAIM" not in seen

    # Prove it's RLS, not missing data or a fluke: with RLS forced off, the
    # identical query (scoped by facility, not app.user_id, to avoid
    # cross-test noise) returns both facilities' claims.
    with owner_engine.connect() as conn:
        conn.execute(text("ALTER TABLE claims NO FORCE ROW LEVEL SECURITY"))
        conn.commit()
        try:
            unforced = (
                conn.execute(
                    text(
                        "SELECT patient_control_number FROM claims "
                        "WHERE facility_id IN (:a, :b)"
                    ),
                    {"a": str(facility_a), "b": str(facility_b)},
                )
                .scalars()
                .all()
            )
        finally:
            conn.execute(text("ALTER TABLE claims FORCE ROW LEVEL SECURITY"))
            conn.commit()
    assert set(unforced) == {"TENANT-A-CLAIM", "TENANT-B-CLAIM"}


def test_rls_blocks_cross_facility_read_even_by_known_id(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    """IDOR-style check: knowing another facility's row id (e.g. a guessed
    or leaked UUID) must not be enough to read it -- RLS applies
    regardless of how precisely the row is targeted, not just to
    unfiltered scans."""
    user_a, _org_a, facility_a = seed_org_facility_user(owner_engine, "IDOR tenant A")
    user_b, _org_b, facility_b = seed_org_facility_user(owner_engine, "IDOR tenant B")
    _seed_claim(app_session_factory, user_a, facility_a, "IDOR-TENANT-A")
    _seed_claim(app_session_factory, user_b, facility_b, "IDOR-TENANT-B")

    with access_session(app_session_factory, user_b) as session:
        rows = session.execute(text("SELECT id FROM claims")).scalars().all()
    assert len(rows) >= 1

    with access_session(app_session_factory, user_a) as session:
        cross_facility_rows = (
            session.execute(text("SELECT id FROM claims WHERE id = :id"), {"id": str(rows[0])})
            .scalars()
            .all()
        )
    assert cross_facility_rows == []


# --- Requirement 2: a billing-company user scoped to specific facilities ------


def test_billing_company_user_scoped_to_two_facilities_cannot_read_a_third(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    """`scope=SPECIFIC_FACILITIES` narrows access to exactly the named
    facilities -- a billing company managing an ASC_GROUP's facilities A
    and B, but not C, must never see C's claims, with app-level filtering
    disabled exactly like the flat cross-facility proof above."""
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        billing_co = repository.create_organization(
            session, parent_org_id=None, type="BILLING_COMPANY", name=f"Billing Co {uuid.uuid4()}"
        )
        asc_group = repository.create_organization(
            session, parent_org_id=None, type="ASC_GROUP", name=f"ASC Group {uuid.uuid4()}"
        )
        facility_a = repository.create_facility(session, asc_group.id, name="Facility A")
        facility_b = repository.create_facility(session, asc_group.id, name="Facility B")
        facility_c = repository.create_facility(session, asc_group.id, name="Facility C")
        user = repository.create_user(session, subject=f"billing-co-user-{uuid.uuid4()}@test")
        repository.create_membership(
            session,
            user.id,
            billing_co.id,
            role=Role.MANAGER.value,
            scope="SPECIFIC_FACILITIES",
            facility_ids=(facility_a.id, facility_b.id),
        )

    _seed_claim(app_session_factory, user.id, facility_a.id, "SCOPED-FACILITY-A")
    _seed_claim(app_session_factory, user.id, facility_b.id, "SCOPED-FACILITY-B")
    # Facility C's claim is written by a separate ASC_GROUP-level admin
    # user (the billing-company user has no access to write it either).
    other_user_id, _org, _facility = seed_org_facility_user(owner_engine, "Facility C owner")
    with session_factory() as session, session.begin():
        repository.create_membership(
            session, other_user_id, asc_group.id, role=Role.ORG_ADMIN.value, scope="ALL_FACILITIES"
        )
    _seed_claim(app_session_factory, other_user_id, facility_c.id, "SCOPED-FACILITY-C")

    with access_session(app_session_factory, user.id) as session:
        seen = set(
            session.execute(text("SELECT patient_control_number FROM claims")).scalars().all()
        )
    assert "SCOPED-FACILITY-A" in seen
    assert "SCOPED-FACILITY-B" in seen
    assert "SCOPED-FACILITY-C" not in seen


# --- Requirement 3: a parent-org user reads child-org facilities --------------


def test_parent_org_user_reads_child_org_facilities(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    """A membership at an ancestor org grants access to every descendant
    org's facilities, per "the one access rule" -- a user holding
    membership only at the parent must still reach a claim that actually
    lives under a child org's facility."""
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        parent_org = repository.create_organization(
            session, parent_org_id=None, type="ASC_GROUP", name=f"Parent org {uuid.uuid4()}"
        )
        child_org = repository.create_organization(
            session, parent_org_id=parent_org.id, type="ASC", name=f"Child org {uuid.uuid4()}"
        )
        child_facility = repository.create_facility(session, child_org.id, name="Child facility")
        user = repository.create_user(session, subject=f"parent-org-user-{uuid.uuid4()}@test")
        # Membership at the PARENT only -- never at child_org directly.
        repository.create_membership(
            session, user.id, parent_org.id, role=Role.ORG_ADMIN.value, scope="ALL_FACILITIES"
        )

    _seed_claim(app_session_factory, user.id, child_facility.id, "CHILD-ORG-CLAIM")

    with access_session(app_session_factory, user.id) as session:
        seen = set(
            session.execute(text("SELECT patient_control_number FROM claims")).scalars().all()
        )
    assert "CHILD-ORG-CLAIM" in seen

    with access_session(app_session_factory, user.id) as session:
        role = repository.resolve_membership_role(session, user.id, child_org.id)
    assert role == Role.ORG_ADMIN.value


# --- Requirement 4: revoking a membership revokes access immediately ----------


def test_revoking_membership_revokes_access_immediately(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    """No token-revocation list exists (`security/session.py`'s module
    docstring) -- immediacy comes entirely from resolving access fresh,
    from `memberships`, on every request. Deleting the membership row
    (an administrative action -- asc_app has no DELETE grant on
    `memberships`, matching every other mutable table's retention
    posture, so this runs via the owner connection) must make the next
    read see nothing, with no caching or stale-token window."""
    user_id, org_id, facility_id = seed_org_facility_user(owner_engine, "Revocation test tenant")
    _seed_claim(app_session_factory, user_id, facility_id, "PRE-REVOCATION-CLAIM")

    with access_session(app_session_factory, user_id) as session:
        before = set(
            session.execute(text("SELECT patient_control_number FROM claims")).scalars().all()
        )
        role_before = repository.resolve_membership_role(session, user_id, org_id)
    assert "PRE-REVOCATION-CLAIM" in before
    assert role_before == Role.ORG_ADMIN.value

    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        session.execute(
            text(
                "DELETE FROM membership_facilities WHERE membership_id IN "
                "(SELECT id FROM memberships WHERE user_id = :uid)"
            ),
            {"uid": str(user_id)},
        )
        session.execute(
            text("DELETE FROM memberships WHERE user_id = :uid"), {"uid": str(user_id)}
        )

    with access_session(app_session_factory, user_id) as session:
        after = set(
            session.execute(text("SELECT patient_control_number FROM claims")).scalars().all()
        )
        role_after = repository.resolve_membership_role(session, user_id, org_id)
    assert after == set()
    assert role_after is None


# --- Requirement 5: analyst gets masked PHI, biller does not ------------------


def test_analyst_gets_masked_phi_biller_does_not(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    """`security/phi_masking.py`'s pure function is unit-tested in
    tests/security/test_phi_masking.py; this proves the real wiring --
    `api.repository.PostgresRepository.get_finding_detail` -- actually
    applies it against a real decrypted claim."""
    encryptor = _make_test_encryptor()
    user_id, org_id, facility_id = seed_org_facility_user(owner_engine, "Masking test tenant")
    claim_id = _seed_claim(
        app_session_factory,
        user_id,
        facility_id,
        "MASKING-TEST-CLAIM",
        patient_name_encrypted=encrypt_phi_field(encryptor, "PATIENT UNMASKED CHECK"),
        patient_member_id_encrypted=encrypt_phi_field(encryptor, "MBR-UNMASKED-000001"),
    )
    finding_id = _seed_finding_for_claim(app_session_factory, user_id, facility_id, claim_id)

    repository_adapter = PostgresRepository(
        app_session_factory, drafter=ScriptedPacketDrafter([]), encryptor=encryptor
    )

    analyst_detail = repository_adapter.get_finding_detail(
        user_id, facility_id, finding_id, actor="analyst-tester", role=Role.ANALYST
    )
    assert analyst_detail is not None
    assert analyst_detail.patient_name == "[MASKED]"
    assert analyst_detail.patient_member_id == "[MASKED]"

    biller_detail = repository_adapter.get_finding_detail(
        user_id, facility_id, finding_id, actor="biller-tester", role=Role.BILLER
    )
    assert biller_detail is not None
    assert biller_detail.patient_name == "PATIENT UNMASKED CHECK"
    assert biller_detail.patient_member_id == "MBR-UNMASKED-000001"


# --- Requirement 6: a five-level hierarchy resolves without looping -----------


def test_five_level_hierarchy_resolves_without_looping(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    """PLATFORM -> BILLING_COMPANY -> ASC_GROUP -> ASC_GROUP -> ASC (with
    a facility) is five levels deep -- a membership at the top must still
    resolve all the way down. This also stands in as the "doesn't loop"
    proof: the cycle-guarded recursive CTE
    (`alembic/versions/0001_initial_schema.py`) has a hard visited-set
    bound, so a query that somehow looped would hang or error rather
    than quietly return the wrong answer -- this test completing with
    the right claim visible is the actual evidence."""
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        level1 = repository.create_organization(
            session, parent_org_id=None, type="PLATFORM", name=f"L1 {uuid.uuid4()}"
        )
        level2 = repository.create_organization(
            session, parent_org_id=level1.id, type="BILLING_COMPANY", name=f"L2 {uuid.uuid4()}"
        )
        level3 = repository.create_organization(
            session, parent_org_id=level2.id, type="ASC_GROUP", name=f"L3 {uuid.uuid4()}"
        )
        level4 = repository.create_organization(
            session, parent_org_id=level3.id, type="ASC_GROUP", name=f"L4 {uuid.uuid4()}"
        )
        level5 = repository.create_organization(
            session, parent_org_id=level4.id, type="ASC", name=f"L5 {uuid.uuid4()}"
        )
        facility = repository.create_facility(session, level5.id, name="Deepest facility")
        user = repository.create_user(session, subject=f"five-level-user-{uuid.uuid4()}@test")
        # Membership only at the very top (level1).
        repository.create_membership(
            session, user.id, level1.id, role=Role.PLATFORM_ADMIN.value, scope="ALL_FACILITIES"
        )

    _seed_claim(app_session_factory, user.id, facility.id, "FIVE-LEVEL-DEEP-CLAIM")

    with access_session(app_session_factory, user.id) as session:
        seen = set(
            session.execute(text("SELECT patient_control_number FROM claims")).scalars().all()
        )
    assert "FIVE-LEVEL-DEEP-CLAIM" in seen
