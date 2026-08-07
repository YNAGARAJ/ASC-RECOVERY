"""Phase 6 (`docs/MASTER-BUILD-PROMPT-V2.md`): an org with its own
`organizations.kms_key_id` encrypts its claims' patient PHI under that
key, not the platform default -- proves the wiring all the way through
`ingestion.pipeline.ingest_file`, not just the pure `encrypt_phi_field`/
`EnvelopeEncryptor` layer `tests/security/` already covers. An org with
no dedicated key still falls back to the encryptor's own default, same
behavior as before this phase.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from db.base import make_session_factory
from db.models import Claim as ClaimModel
from db.models import Organization as OrganizationModel
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import ingest_file
from ingestion.virus_scan import EicarAwareScanner
from security.encryption import EnvelopeEncryptor
from security.kms_local import LocalKMS
from tests.domain.fixtures_x835 import minimal_valid_835
from tests.ingestion.conftest import seed_org_with_contract


def _claim_patient_name_column(
    app_session_factory: sessionmaker[Session], user_id: uuid.UUID, facility_id: uuid.UUID
) -> str:
    with access_session(app_session_factory, user_id) as session:
        row = session.execute(
            select(ClaimModel).where(ClaimModel.facility_id == facility_id)
        ).scalar_one()
        assert row.patient_name_encrypted is not None
        return row.patient_name_encrypted


def test_claim_encrypts_under_the_orgs_own_dedicated_kms_key(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = seed_org_with_contract(owner_engine, "Org KMS key tenant")
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        org_id = repository.get_org_id_for_facility(session, facility_id)
        assert org_id is not None
        org = session.get(OrganizationModel, org_id)
        assert org is not None
        org.kms_key_id = "org-dedicated-kek"

    kms = LocalKMS()
    kms.generate_kek("platform-default-kek")
    kms.generate_kek("org-dedicated-kek")
    encryptor = EnvelopeEncryptor(kms)

    with access_session(app_session_factory, user_id) as session:
        outcome = ingest_file(
            session,
            facility_id,
            content=minimal_valid_835().encode("utf-8"),
            source="upload",
            uploaded_by="tester",
            scanner=EicarAwareScanner(),
            encryptor=encryptor,
        )
    assert isinstance(outcome, IngestionOutcome)
    assert outcome.status == "ingested"

    raw_name_column = _claim_patient_name_column(app_session_factory, user_id, facility_id)
    assert json.loads(raw_name_column)["kek_id"] == "org-dedicated-kek"


def test_claim_encrypts_under_the_platform_default_when_the_org_has_no_dedicated_key(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = seed_org_with_contract(owner_engine, "No dedicated KMS key tenant")
    kms = LocalKMS()
    kms.generate_kek("platform-default-kek")
    encryptor = EnvelopeEncryptor(kms)

    with access_session(app_session_factory, user_id) as session:
        outcome = ingest_file(
            session,
            facility_id,
            content=minimal_valid_835().encode("utf-8"),
            source="upload",
            uploaded_by="tester",
            scanner=EicarAwareScanner(),
            encryptor=encryptor,
        )
    assert isinstance(outcome, IngestionOutcome)
    assert outcome.status == "ingested"

    raw_name_column = _claim_patient_name_column(app_session_factory, user_id, facility_id)
    assert json.loads(raw_name_column)["kek_id"] == "platform-default-kek"
