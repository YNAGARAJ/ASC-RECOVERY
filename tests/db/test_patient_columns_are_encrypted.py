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

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from db.models import Claim as ClaimModel
from security.encryption import EnvelopeEncryptor
from security.kms_local import LocalKMS
from security.phi_columns import decrypt_phi_field, encrypt_phi_field
from tests.db.conftest import seed_org_facility_user

_PLAINTEXT_NAME = "A VERY IDENTIFIABLE PATIENT NAME"
_PLAINTEXT_MEMBER_ID = "MBR-0000000042"


def _new_facility(owner_engine: Engine, label: str) -> tuple[uuid.UUID, uuid.UUID]:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, label)
    return user_id, facility_id


def test_patient_columns_hold_ciphertext_not_plaintext(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = _new_facility(owner_engine, "Encrypted columns tenant")
    kms = LocalKMS()
    kms.generate_kek("test-kek")
    encryptor = EnvelopeEncryptor(kms)

    with access_session(app_session_factory, user_id) as session:
        remittance, _ = repository.record_remittance_if_new(
            session, facility_id, uuid.uuid4().hex, source="upload", uploaded_by="tester"
        )
        claim = repository.create_claim(
            session,
            facility_id,
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

    with access_session(app_session_factory, user_id) as session:
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
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = _new_facility(owner_engine, "Null patient columns tenant")

    with access_session(app_session_factory, user_id) as session:
        remittance, _ = repository.record_remittance_if_new(
            session, facility_id, uuid.uuid4().hex, source="upload", uploaded_by="tester"
        )
        claim = repository.create_claim(
            session,
            facility_id,
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

    with access_session(app_session_factory, user_id) as session:
        row = session.get(ClaimModel, claim_id)
        assert row is not None
        assert row.patient_name_encrypted is None
        assert row.patient_member_id_encrypted is None
