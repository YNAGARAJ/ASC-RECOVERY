"""Phase 9 (`docs/MASTER-BUILD-PROMPT-V2.md`): 837 claim file ingestion
against real Postgres -- an 837 enriches an already-835-ingested claim
with diagnosis codes/rendering provider, correlated by
`patient_control_number`, never creating a claim/finding of its own.
Requires a live Postgres -- see tests/ingestion/conftest.py. Written
here, never executed in this environment (no Docker/WSL/Postgres
available) -- honest about being unverified, same as the rest of this
repo's DB-backed test files.
"""

from __future__ import annotations

import json

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db import repository as db_repository
from db.access import access_session
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import ClaimFileOutcome, ingest_file
from ingestion.virus_scan import EicarAwareScanner
from security.phi_columns import decrypt_phi_field
from tests.domain.fixtures_x835 import minimal_valid_835
from tests.ingestion.conftest import make_test_encryptor, seed_org_with_contract

_ELEMENT_SEP = "*"
_SUB_ELEMENT_SEP = ":"
_SEGMENT_TERM = "~"
_PATIENT_CONTROL_NUMBER = "CLAIM0001"  # matches tests.domain.fixtures_x835's claim_segments()


def _seg(*fields: str) -> str:
    return _ELEMENT_SEP.join(fields)


def _minimal_837(patient_control_number: str) -> str:
    filing_code = f"11{_SUB_ELEMENT_SEP}B{_SUB_ELEMENT_SEP}1"
    segments = [
        _seg(
            "ISA", "00", "          ", "00", "          ", "ZZ", "SENDERID       ", "ZZ",
            "RECEIVERID     ", "230101", "1200", "^", "00501", "000000905", "0", "T",
            _SUB_ELEMENT_SEP,
        ),
        _seg("GS", "HC", "SENDERID", "RECEIVERID", "20230101", "1200", "1", "X", "005010X222A1"),
        _seg("ST", "837", "0001", "005010X222A1"),
        _seg("CLM", patient_control_number, "500", "", "", filing_code),
        _seg("HI", f"ABK{_SUB_ELEMENT_SEP}E119", f"ABF{_SUB_ELEMENT_SEP}I10"),
        _seg("NM1", "82", "1", "RENDERING", "PROVIDER", "", "", "", "XX", "1999999999"),
        _seg("SE", "1", "0001"),
        _seg("GE", "1", "1"),
        _seg("IEA", "1", "000000905"),
    ]
    return _SEGMENT_TERM.join(segments) + _SEGMENT_TERM


def test_837_enriches_an_already_ingested_claim(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = seed_org_with_contract(owner_engine, "837 enrichment tenant")
    encryptor = make_test_encryptor()
    scanner = EicarAwareScanner()

    with access_session(app_session_factory, user_id) as session:
        outcome_835 = ingest_file(
            session,
            facility_id,
            content=minimal_valid_835().encode("utf-8"),
            source="upload",
            uploaded_by="tester",
            scanner=scanner,
            encryptor=encryptor,
        )
    assert isinstance(outcome_835, IngestionOutcome)
    assert outcome_835.status == "ingested"

    with access_session(app_session_factory, user_id) as session:
        outcome_837 = ingest_file(
            session,
            facility_id,
            content=_minimal_837(_PATIENT_CONTROL_NUMBER).encode("utf-8"),
            source="upload",
            uploaded_by="tester",
            scanner=scanner,
            encryptor=encryptor,
        )
    assert isinstance(outcome_837, ClaimFileOutcome)
    assert outcome_837.status == "ingested"
    assert outcome_837.claims_enriched == 1
    assert outcome_837.claims_unmatched == 0

    with access_session(app_session_factory, user_id) as session:
        (claim,) = db_repository.get_claims_by_patient_control_number(
            session, facility_id, _PATIENT_CONTROL_NUMBER
        )
        assert claim.rendering_provider_name == "RENDERING"
        diagnosis_codes = json.loads(
            decrypt_phi_field(encryptor, claim.diagnosis_codes_encrypted) or "[]"
        )
        assert diagnosis_codes == ["E119", "I10"]


def test_837_with_no_matching_claim_does_not_quarantine_the_file(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    """A missing 835 counterpart is an expected, recoverable ordering
    situation (the 837 arrived first, or its 835 never will) -- not
    evidence of a financial-integrity bug the way an unmatched reversal
    is (Part 1, F-01). The claim is skipped and counted, the file still
    ingests successfully."""
    user_id, facility_id = seed_org_with_contract(owner_engine, "837 unmatched tenant")
    encryptor = make_test_encryptor()
    scanner = EicarAwareScanner()

    with access_session(app_session_factory, user_id) as session:
        outcome = ingest_file(
            session,
            facility_id,
            content=_minimal_837("NEVER-INGESTED-0001").encode("utf-8"),
            source="upload",
            uploaded_by="tester",
            scanner=scanner,
            encryptor=encryptor,
        )
    assert isinstance(outcome, ClaimFileOutcome)
    assert outcome.status == "ingested"
    assert outcome.claims_enriched == 0
    assert outcome.claims_unmatched == 1
    assert outcome.quarantine_reason is None
