"""Phase 6 (`docs/MASTER-BUILD-PROMPT-V2.md`): `get_organization_kms_key_id`
in isolation -- `tests/ingestion/test_apply_org_kms_key.py` proves the
full ingestion-time wiring; this proves the lookup itself, including the
"no dedicated key" default `None` case.
"""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from db.base import make_session_factory
from db.models import Organization as OrganizationModel
from tests.db.conftest import seed_org_facility_user


def test_returns_none_when_the_org_has_no_dedicated_key(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    user_id, org_id, _ = seed_org_facility_user(owner_engine, "Org kms key lookup test")

    with access_session(app_session_factory, user_id) as session:
        kms_key_id = repository.get_organization_kms_key_id(session, org_id)

    assert kms_key_id is None


def test_returns_the_configured_key_id(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    user_id, org_id, _ = seed_org_facility_user(owner_engine, "Org kms key configured test")
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        org = session.get(OrganizationModel, org_id)
        assert org is not None
        org.kms_key_id = "arn:aws:kms:us-east-1:123456789012:key/example-org-key"

    with access_session(app_session_factory, user_id) as session:
        kms_key_id = repository.get_organization_kms_key_id(session, org_id)

    assert kms_key_id == "arn:aws:kms:us-east-1:123456789012:key/example-org-key"
