"""Phase 5 step 6's RLS proof: `org_policies`' `org_access` policy (0007,
shared with `api_keys`/`invitations`) is what lets an org-resolved caller
read/write their own org's policy, and denies an outsider both -- same
shape as `tests/db/test_api_keys.py`. Unlike `revoke_membership`/
`revoke_api_key` (an `UPDATE` that just matches zero rows for an
inaccessible target), `upsert_org_policy`'s first-write-ever path is an
`INSERT`, so an outsider's attempt fails loudly (`DBAPIError`, the RLS
`WITH CHECK` clause rejecting the row) rather than quietly returning
nothing -- this can only happen by calling the DB layer directly, never
through the API (which always writes `ctx.org_id`, never a client-
supplied or otherwise-inaccessible one).
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from tests.db.conftest import seed_org_facility_user


def test_missing_policy_returns_none(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(owner_engine, "Org policy missing-row test")
    with access_session(app_session_factory, admin_id) as session:
        policy = repository.get_org_policy(session, org_id)
    assert policy is None


def test_org_admin_can_set_and_read_back_the_policy(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(owner_engine, "Org policy round-trip test")
    with access_session(app_session_factory, admin_id) as session:
        created = repository.upsert_org_policy(
            session, org_id, session_timeout_seconds=1800, ip_allowlist=["203.0.113.5"]
        )
    assert created.session_timeout_seconds == 1800
    assert created.ip_allowlist == ["203.0.113.5"]
    assert created.mfa_required is True

    with access_session(app_session_factory, admin_id) as session:
        fetched = repository.get_org_policy(session, org_id)
    assert fetched is not None
    assert fetched.session_timeout_seconds == 1800
    assert fetched.ip_allowlist == ["203.0.113.5"]


def test_setting_the_policy_a_second_time_updates_rather_than_duplicates(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(owner_engine, "Org policy update test")
    with access_session(app_session_factory, admin_id) as session:
        repository.upsert_org_policy(
            session, org_id, session_timeout_seconds=1800, ip_allowlist=["203.0.113.5"]
        )
    with access_session(app_session_factory, admin_id) as session:
        updated = repository.upsert_org_policy(
            session, org_id, session_timeout_seconds=3600, ip_allowlist=None
        )
    assert updated.session_timeout_seconds == 3600
    assert updated.ip_allowlist is None

    with access_session(app_session_factory, admin_id) as session:
        fetched = repository.get_org_policy(session, org_id)
    assert fetched is not None
    assert fetched.session_timeout_seconds == 3600
    assert fetched.ip_allowlist is None


def test_data_residency_region_round_trips_and_defaults_to_none(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(owner_engine, "Org policy data residency test")
    with access_session(app_session_factory, admin_id) as session:
        created = repository.upsert_org_policy(
            session,
            org_id,
            session_timeout_seconds=None,
            ip_allowlist=None,
            data_residency_region="eu-west-1",
        )
    assert created.data_residency_region == "eu-west-1"

    with access_session(app_session_factory, admin_id) as session:
        fetched = repository.get_org_policy(session, org_id)
    assert fetched is not None
    assert fetched.data_residency_region == "eu-west-1"

    # Omitting it on a later write clears it, same full-replace semantics
    # as session_timeout_seconds/ip_allowlist.
    with access_session(app_session_factory, admin_id) as session:
        updated = repository.upsert_org_policy(
            session, org_id, session_timeout_seconds=None, ip_allowlist=None
        )
    assert updated.data_residency_region is None


def test_caller_outside_the_org_reads_nothing(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_a, _ = seed_org_facility_user(owner_engine, "Org policy isolation test A")
    outsider_id, _, _ = seed_org_facility_user(owner_engine, "Org policy isolation test B")
    with access_session(app_session_factory, admin_id) as session:
        repository.upsert_org_policy(
            session, org_a, session_timeout_seconds=1800, ip_allowlist=[]
        )

    with access_session(app_session_factory, outsider_id) as session:
        policy = repository.get_org_policy(session, org_a)
    assert policy is None


def test_caller_outside_the_org_cannot_create_a_policy_row(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    _, org_a, _ = seed_org_facility_user(owner_engine, "Org policy write-isolation test A")
    outsider_id, _, _ = seed_org_facility_user(owner_engine, "Org policy write-isolation test B")

    with pytest.raises(DBAPIError):
        with access_session(app_session_factory, outsider_id) as session:
            repository.upsert_org_policy(
                session, org_a, session_timeout_seconds=1800, ip_allowlist=[]
            )
