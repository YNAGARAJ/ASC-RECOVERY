"""Phase 10 gate: `claims.patient_name_encrypted` /
`patient_member_id_encrypted` never hold plaintext PHI at rest -- only an
EnvelopeEncryptor-produced ciphertext blob that decrypts back to the
original value. Complements tests/ingestion/test_apply_audit_entry.py
(which proves the write path calls encryption at all) by proving what's
actually sitting in the column afterward.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.models import Claim as ClaimModel
from db.tenancy import tenant_session
from security.encryption import EnvelopeEncryptor
from security.kms_local import LocalKMS
from security.phi_columns import decrypt_phi_field, encrypt_phi_field

_PLAINTEXT_NAME = "A VERY IDENTIFIABLE PATIENT NAME"
_PLAINTEXT_MEMBER_ID = "MBR-0000000042"


def _new_tenant(session_factory: sessionmaker[Session], label: str) -> uuid.UUID:
    with session_factory() as session, session.begin():
        tenant = repository.create_tenant(session, f"{label} {uuid.uuid4()}")
        return tenant.id


def test_patient_columns_hold_ciphertext_not_plaintext(
    app_session_factory: sessionmaker[Session],
) -> None:
    tenant_id = _new_tenant(app_session_factory, "Encrypted columns tenant")
    kms = LocalKMS()
    kms.generate_kek("test-kek")
    encryptor = EnvelopeEncryptor(kms)

    with tenant_session(app_session_factory, tenant_id) as session:
        remittance, _ = repository.record_remittance_if_new(
            session, tenant_id, uuid.uuid4().hex, source="upload", uploaded_by="tester"
        )
        claim = repository.create_claim(
            session,
            tenant_id,
            remittance.id,
            patient_control_number="ENCRYPTION-TEST-CLAIM",
            payer_claim_control_number="PAYERCTRL-ENC-1",
            status="1",
            date_of_service=date(2023, 1, 10),
            total_charge=Decimal("500.00"),
            total_paid_reported=Decimal("430.00"),
            patient_responsibility=Decimal("20.00"),
            patient_name_encrypted=encrypt_phi_field(encryptor, _PLAINTEXT_NAME),
            patient_member_id_encrypted=encrypt_phi_field(encryptor, _PLAINTEXT_MEMBER_ID),
        )
        claim_id = claim.id

    with tenant_session(app_session_factory, tenant_id) as session:
        row = session.get(ClaimModel, claim_id)
        assert row is not None
        raw_name_column = row.patient_name_encrypted
        raw_member_id_column = row.patient_member_id_encrypted

    assert raw_name_column is not None
    assert _PLAINTEXT_NAME not in raw_name_column
    assert raw_member_id_column is not None
    assert _PLAINTEXT_MEMBER_ID not in raw_member_id_column

    assert decrypt_phi_field(encryptor, raw_name_column) == _PLAINTEXT_NAME
    assert decrypt_phi_field(encryptor, raw_member_id_column) == _PLAINTEXT_MEMBER_ID


def test_patient_columns_are_null_when_no_patient_info_given(
    app_session_factory: sessionmaker[Session],
) -> None:
    tenant_id = _new_tenant(app_session_factory, "Null patient columns tenant")

    with tenant_session(app_session_factory, tenant_id) as session:
        remittance, _ = repository.record_remittance_if_new(
            session, tenant_id, uuid.uuid4().hex, source="upload", uploaded_by="tester"
        )
        claim = repository.create_claim(
            session,
            tenant_id,
            remittance.id,
            patient_control_number="NO-PATIENT-INFO-CLAIM",
            payer_claim_control_number="PAYERCTRL-ENC-2",
            status="1",
            date_of_service=date(2023, 1, 10),
            total_charge=Decimal("500.00"),
            total_paid_reported=Decimal("430.00"),
            patient_responsibility=Decimal("20.00"),
        )
        claim_id = claim.id

    with tenant_session(app_session_factory, tenant_id) as session:
        row = session.get(ClaimModel, claim_id)
        assert row is not None
        assert row.patient_name_encrypted is None
        assert row.patient_member_id_encrypted is None
