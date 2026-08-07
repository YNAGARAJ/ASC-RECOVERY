"""Thin persistence functions bridging the Postgres schema and the Phase 1
domain layer.

Money and Rate values cross the boundary as `Decimal` in both directions --
never `float` (CLAUDE.md rule 2). JSONB rule sub-structures on
contract_versions store rates as decimal strings, not JSON numbers, for the
same reason: a JSON number is parsed back as `float` by every standard
JSON decoder, silently reintroducing the exact class of bug `domain.money`
exists to make impossible.

Every function here expects to run inside a `db.access.access_session()`
transaction -- RLS enforces access isolation at the database level;
nothing here adds its own `WHERE facility_id = ...`/`WHERE org_id = ...`
filtering on top of what the caller already scopes queries to, since
Phase 4's whole point is that RLS (via `resolve_accessible_facility_ids`/
`resolve_accessible_org_ids`) is the actual boundary, not app-level
filtering. Business/PHI tables are scoped by `facility_id`; payer
contracts (negotiated per organization, not per facility) are scoped by
`org_id` -- see `db/models.py`'s module docstring.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.access_history import AccessEvent, merge_access_history
from db.models import Adjustment as AdjustmentModel
from db.models import ApiKey as ApiKeyModel
from db.models import AuditLog as AuditLogModel
from db.models import Claim as ClaimModel
from db.models import Contract as ContractModel
from db.models import ContractVersion as ContractVersionModel
from db.models import Facility as FacilityModel
from db.models import FeeScheduleLine as FeeScheduleLineModel
from db.models import Finding as FindingModel
from db.models import Invitation as InvitationModel
from db.models import InvitationFacility as InvitationFacilityModel
from db.models import Membership as MembershipModel
from db.models import MembershipFacility as MembershipFacilityModel
from db.models import Organization as OrganizationModel
from db.models import PHIAccessLog as PHIAccessLogModel
from db.models import RecoveryPacket as RecoveryPacketModel
from db.models import Remittance as RemittanceModel
from db.models import ServiceLine as ServiceLineModel
from db.models import User as UserModel
from db.rules_version import RULES_VERSION
from domain.contract import (
    AssistantSurgeonRule,
    BilateralConvention,
    BilateralRule,
    CaseRateGroup,
    ContractVersion,
    ImplantCarveoutRule,
    MPPRRule,
    PricingMethod,
    find_effective_contract,
)
from domain.money import Money, Rate
from domain.variance import Finding

# Generous bound on how many levels of the org hierarchy
# resolve_membership_role() will walk before giving up -- well beyond the
# "five-level hierarchy resolves without looping" requirement, just a
# hard stop against a corrupted parent_org_id cycle (RLS's own
# resolution functions guard the same risk with an explicit visited-array
# CTE; this is the equivalent guard for the Python-side ancestor walk).
_MAX_HIERARCHY_DEPTH = 20

# --- JSONB (de)serialization for the contract rule sub-structures ------------


def _rate_to_str(rate: Rate) -> str:
    return str(rate.as_decimal())


def _mppr_rule_to_json(rule: MPPRRule) -> dict[str, Any]:
    return {
        "enabled": rule.enabled,
        "second_procedure_rate": _rate_to_str(rule.second_procedure_rate),
        "third_and_subsequent_rate": _rate_to_str(rule.third_and_subsequent_rate),
        "exempt_codes": sorted(rule.exempt_codes),
    }


def _mppr_rule_from_json(data: dict[str, Any]) -> MPPRRule:
    return MPPRRule(
        enabled=bool(data["enabled"]),
        second_procedure_rate=Rate(str(data["second_procedure_rate"])),
        third_and_subsequent_rate=Rate(str(data["third_and_subsequent_rate"])),
        exempt_codes=frozenset(str(c) for c in data["exempt_codes"]),
    )


def _bilateral_rule_to_json(rule: BilateralRule) -> dict[str, Any]:
    return {
        "enabled": rule.enabled,
        "total_rate": _rate_to_str(rule.total_rate),
        "convention": rule.convention.value,
    }


def _bilateral_rule_from_json(data: dict[str, Any]) -> BilateralRule:
    return BilateralRule(
        enabled=bool(data["enabled"]),
        total_rate=Rate(str(data["total_rate"])),
        convention=BilateralConvention(str(data["convention"])),
    )


def _assistant_surgeon_rule_to_json(rule: AssistantSurgeonRule) -> dict[str, Any]:
    return {
        "enabled": rule.enabled,
        "rate": _rate_to_str(rule.rate),
        "applicable_modifiers": sorted(rule.applicable_modifiers),
    }


def _assistant_surgeon_rule_from_json(data: dict[str, Any]) -> AssistantSurgeonRule:
    return AssistantSurgeonRule(
        enabled=bool(data["enabled"]),
        rate=Rate(str(data["rate"])),
        applicable_modifiers=frozenset(str(m) for m in data["applicable_modifiers"]),
    )


def _implant_carveout_rule_to_json(rule: ImplantCarveoutRule) -> dict[str, Any]:
    return {
        "enabled": rule.enabled,
        "procedure_codes": sorted(rule.procedure_codes),
        "revenue_codes": sorted(rule.revenue_codes),
    }


def _implant_carveout_rule_from_json(data: dict[str, Any]) -> ImplantCarveoutRule:
    return ImplantCarveoutRule(
        enabled=bool(data["enabled"]),
        procedure_codes=frozenset(str(c) for c in data["procedure_codes"]),
        revenue_codes=frozenset(str(c) for c in data["revenue_codes"]),
    )


def _case_rate_groups_to_json(groups: tuple[CaseRateGroup, ...]) -> list[dict[str, Any]]:
    return [
        {
            "trigger_procedure_codes": sorted(group.trigger_procedure_codes),
            "flat_rate": str(group.flat_rate.as_decimal()),
            "includes_implants": group.includes_implants,
        }
        for group in groups
    ]


def _case_rate_groups_from_json(data: list[Any]) -> tuple[CaseRateGroup, ...]:
    return tuple(
        CaseRateGroup(
            trigger_procedure_codes=frozenset(str(c) for c in group["trigger_procedure_codes"]),
            flat_rate=Money(str(group["flat_rate"])),
            includes_implants=bool(group["includes_implants"]),
        )
        for group in data
    )


def _contract_version_to_domain(
    session: Session, row: ContractVersionModel, payer_id: str
) -> ContractVersion:
    fee_schedule_rows = (
        session.execute(
            select(FeeScheduleLineModel).where(
                FeeScheduleLineModel.contract_version_id == row.id
            )
        )
        .scalars()
        .all()
    )
    fee_schedule = {r.procedure_code: Money(r.allowed_amount) for r in fee_schedule_rows}
    return ContractVersion(
        payer_id=payer_id,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        default_pricing_method=PricingMethod(row.default_pricing_method),
        fee_schedule=fee_schedule,
        percent_of_charge_rate=(
            Rate(row.percent_of_charge_rate) if row.percent_of_charge_rate is not None else None
        ),
        case_rate_groups=_case_rate_groups_from_json(row.case_rate_groups),
        mppr_rule=_mppr_rule_from_json(row.mppr_rule),
        bilateral_rule=_bilateral_rule_from_json(row.bilateral_rule),
        assistant_surgeon_rule=_assistant_surgeon_rule_from_json(row.assistant_surgeon_rule),
        implant_carveout_rule=_implant_carveout_rule_from_json(row.implant_carveout_rule),
    )


# --- Organizations, facilities, memberships (Phase 4) ---------------------------


def get_org_id_for_facility(session: Session, facility_id: uuid.UUID) -> uuid.UUID | None:
    """Contracts are org-scoped (`db/models.py`'s module docstring), but
    ingestion only ever knows which *facility* a remittance belongs to --
    this is the one join point where a facility-scoped write needs to
    reach its parent org's contracts (`ingestion/pipeline.py`)."""
    return session.execute(
        select(FacilityModel.org_id).where(FacilityModel.id == facility_id)
    ).scalar_one_or_none()


def create_organization(
    session: Session,
    *,
    parent_org_id: uuid.UUID | None,
    type: str,
    name: str,
    settings: dict[str, Any] | None = None,
) -> OrganizationModel:
    org = OrganizationModel(
        parent_org_id=parent_org_id, type=type, name=name, settings=settings or {}
    )
    session.add(org)
    session.flush()
    return org


def create_facility(
    session: Session,
    org_id: uuid.UUID,
    *,
    name: str,
    npi: str | None = None,
    tax_id: str | None = None,
    address: str | None = None,
) -> FacilityModel:
    facility = FacilityModel(org_id=org_id, name=name, npi=npi, tax_id=tax_id, address=address)
    session.add(facility)
    session.flush()
    return facility


def create_membership(
    session: Session,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    *,
    role: str,
    scope: str,
    facility_ids: Sequence[uuid.UUID] = (),
) -> MembershipModel:
    """`facility_ids` is only meaningful (and should be non-empty) when
    `scope="SPECIFIC_FACILITIES"` -- ignored for `scope="ALL_FACILITIES"`,
    where access is the full resolved subtree instead
    (`resolve_accessible_facility_ids`, `alembic/versions/0001_initial_schema.py`)."""
    membership = MembershipModel(user_id=user_id, org_id=org_id, role=role, scope=scope)
    session.add(membership)
    session.flush()
    for facility_id in facility_ids:
        session.add(
            MembershipFacilityModel(membership_id=membership.id, facility_id=facility_id)
        )
    session.flush()
    return membership


def revoke_membership(session: Session, membership_id: uuid.UUID) -> bool:
    """Offboarding (Phase 5 step 4): soft-revokes a membership -- sets
    `revoked_at`, an `UPDATE` (the only write `asc_app` is ever granted on
    a mutable table). No `org_id`/`user_id` filter here beyond
    `membership_id` and "not already revoked" -- same convention as every
    other function in this module: RLS (the `org_authoring_update` policy,
    `alembic/versions/0007_user_lifecycle.py`) is the actual boundary on
    *which* membership rows a given caller can reach, not app-level
    filtering. Returns `False` for a membership that doesn't exist, isn't
    visible/writable to this caller, or is already revoked -- callers
    (api/repository.py) treat all three identically, a 404, since RLS
    deliberately makes "doesn't exist" and "not accessible" indistinguishable."""
    result = session.execute(
        update(MembershipModel)
        .where(MembershipModel.id == membership_id, MembershipModel.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    return result.rowcount > 0


def get_default_membership_org_id(session: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    """The org of this user's oldest membership, or `None` if they hold
    none at all. A deliberate, documented Phase 4 stopgap for "which org
    becomes active at login" -- picking among several memberships is a
    real UX decision (a Phase 5 login/switch-org flow's job); this just
    needs to pick *something* deterministic so login can issue a token at
    all. Must run inside an `access_session` scoped to `user_id`, same as
    `resolve_membership_role`."""
    return session.execute(
        select(MembershipModel.org_id)
        .where(MembershipModel.user_id == user_id, MembershipModel.revoked_at.is_(None))
        .order_by(MembershipModel.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()


def get_default_facility_id_for_org(
    session: Session, user_id: uuid.UUID, org_id: uuid.UUID
) -> uuid.UUID | None:
    """The single facility a resolved-accessible set narrows to, if
    exactly one -- `None` if there are zero or more than one (an
    ambiguous case that needs a real facility switcher, Phase 5/12's
    job, not a guess). Common case this resolves cleanly: an `ASC`-type
    org with exactly one facility, the large majority of real customers
    at Phase 4's stage. Must run inside an `access_session` scoped to
    `user_id`, same as `resolve_membership_role`."""
    rows = session.execute(
        select(FacilityModel.id)
        .join(OrganizationModel, FacilityModel.org_id == OrganizationModel.id)
        .where(OrganizationModel.id == org_id)
        .limit(2)
    ).scalars().all()
    return rows[0] if len(rows) == 1 else None


def resolve_membership_role(
    session: Session, user_id: uuid.UUID, org_id: uuid.UUID
) -> str | None:
    """Walks from `org_id` up through `parent_org_id` looking for the
    nearest membership this user holds -- "a user may access a record if
    they hold a membership in the org owning it *or an ancestor org*"
    (the one access rule) applies to role resolution the same way it
    applies to row visibility. Returns `None` if no qualifying membership
    exists anywhere up the chain (revoked, never existed, or the org
    itself is gone) -- callers must treat that as unauthenticated, not as
    "assume some default role."

    Must run inside an `access_session` already scoped to `user_id`
    (`app.user_id` set) -- `memberships`/`organizations` RLS depends on
    it, same bootstrap ordering as every other resolved-access query."""
    current_org_id: uuid.UUID | None = org_id
    visited: set[uuid.UUID] = set()
    for _ in range(_MAX_HIERARCHY_DEPTH):
        if current_org_id is None or current_org_id in visited:
            return None
        visited.add(current_org_id)
        membership = session.execute(
            select(MembershipModel).where(
                MembershipModel.user_id == user_id,
                MembershipModel.org_id == current_org_id,
                MembershipModel.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        if membership is not None:
            return membership.role
        org = session.get(OrganizationModel, current_org_id)
        if org is None:
            return None
        current_org_id = org.parent_org_id
    return None


@dataclass(frozen=True, slots=True)
class OrgMember:
    """One row of `GET /organizations/members` (Phase 5 step 2) --
    a `Membership` joined with the user's `subject` (memberships carries no
    human-readable identity of its own, see `db.models.User`'s docstring)
    and, for a `SPECIFIC_FACILITIES`-scoped membership, the facility_ids
    it's narrowed to (empty for `ALL_FACILITIES`, where access is the full
    resolved subtree instead, not "no facilities")."""

    membership: MembershipModel
    subject: str
    facility_ids: list[uuid.UUID]


def list_org_memberships(
    session: Session, org_id: uuid.UUID, *, limit: int = 20, offset: int = 0
) -> tuple[list[OrgMember], int]:
    """Active (non-revoked) memberships directly on this one org -- not the
    resolved subtree; a caller wanting the whole tree calls this once per
    org_id they can resolve, same pattern as `list_contracts`. Requires the
    `org_authoring_select` RLS policy
    (`alembic/versions/0008_membership_read_policy.py`); a caller without
    resolved access to `org_id` sees zero rows, never an error -- same "no
    such thing as a global read" contract as every other resolved-access
    query in this module."""
    total = session.execute(
        select(func.count())
        .select_from(MembershipModel)
        .where(MembershipModel.org_id == org_id, MembershipModel.revoked_at.is_(None))
    ).scalar_one()
    rows = (
        session.execute(
            select(MembershipModel)
            .where(MembershipModel.org_id == org_id, MembershipModel.revoked_at.is_(None))
            .order_by(MembershipModel.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    members = []
    for membership in rows:
        user = session.get(UserModel, membership.user_id)
        if user is None:
            raise RuntimeError(
                f"membership {membership.id} references a user that no longer "
                "exists -- FK constraints should make this impossible"
            )
        facility_ids = list(
            session.execute(
                select(MembershipFacilityModel.facility_id).where(
                    MembershipFacilityModel.membership_id == membership.id
                )
            )
            .scalars()
            .all()
        )
        members.append(
            OrgMember(membership=membership, subject=user.subject, facility_ids=facility_ids)
        )
    return members, total


# --- Invitations (Phase 5 step 3) -------------------------------------------------


def create_invitation(
    session: Session,
    org_id: uuid.UUID,
    *,
    subject: str,
    role: str,
    scope: str,
    invited_by: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
    facility_ids: Sequence[uuid.UUID] = (),
) -> InvitationModel:
    """Ordinary RLS-scoped write -- must run inside `access_session` as
    the inviting (already-authenticated) user, same as `create_membership`.
    `token_hash` is computed by the caller (`security.tokens.hash_token`);
    this function never sees the raw token."""
    invitation = InvitationModel(
        org_id=org_id,
        subject=subject,
        role=role,
        scope=scope,
        invited_by=invited_by,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    session.add(invitation)
    session.flush()
    for facility_id in facility_ids:
        session.add(
            InvitationFacilityModel(invitation_id=invitation.id, facility_id=facility_id)
        )
    session.flush()
    return invitation


@dataclass(frozen=True, slots=True)
class InvitationPreview:
    """What `get_invitation_by_token_hash` (the `SECURITY DEFINER`
    function, `alembic/versions/0007_user_lifecycle.py`) returns -- an
    anonymous, token-authorized read, so this runs on a plain session, not
    `access_session` (there is no `user_id` yet; see that function's own
    docstring for why it bypasses RLS by design)."""

    id: uuid.UUID
    org_id: uuid.UUID
    subject: str
    role: str
    scope: str
    status: str
    expires_at: datetime


def get_invitation_by_token_hash(session: Session, token_hash: str) -> InvitationPreview | None:
    row = session.execute(
        text(
            "SELECT id, org_id, subject, role, scope, status, expires_at "
            "FROM get_invitation_by_token_hash(:token_hash)"
        ),
        {"token_hash": token_hash},
    ).mappings().one_or_none()
    if row is None:
        return None
    return InvitationPreview(**row)


@dataclass(frozen=True, slots=True)
class AcceptedInvitation:
    user_id: uuid.UUID
    membership_id: uuid.UUID


def accept_invitation(
    session: Session, token_hash: str, *, password_hash: str, mfa_secret_encrypted: str
) -> AcceptedInvitation:
    """Calls the `accept_invitation` `SECURITY DEFINER` function -- same
    anonymous, plain-session, no-`access_session` treatment as
    `get_invitation_by_token_hash`. The function itself raises (a Postgres
    exception, not a Python one) if the invitation is missing, not
    pending, or expired; callers must check those conditions via a
    preceding `get_invitation_by_token_hash` call in the common case
    (api/repository.py does), since letting that exception surface
    uncaught here would fall through to api/errors.py's generic 500
    handler -- correct only as a rare-race fallback, not the primary
    control-flow path."""
    row = session.execute(
        text(
            "SELECT user_id, membership_id FROM "
            "accept_invitation(:token_hash, :password_hash, :mfa_secret_encrypted)"
        ),
        {
            "token_hash": token_hash,
            "password_hash": password_hash,
            "mfa_secret_encrypted": mfa_secret_encrypted,
        },
    ).mappings().one()
    return AcceptedInvitation(**row)


# --- Users (ungated, like organizations/facilities -- see db.models.User's docstring) --


def get_user_by_subject(session: Session, subject: str) -> UserModel | None:
    """Plain, non-access-scoped lookup -- this is how a request's
    identity gets bootstrapped in the first place (src/api/auth.py), so
    it must run outside `access_session` rather than inside it."""
    return session.execute(
        select(UserModel).where(UserModel.subject == subject, UserModel.deleted_at.is_(None))
    ).scalar_one_or_none()


def create_user(
    session: Session,
    *,
    subject: str,
    password_hash: str | None = None,
    mfa_secret_encrypted: str | None = None,
) -> UserModel:
    """No role/org/facility here -- a user's access is entirely defined
    by their `Membership` rows, created separately via
    `create_membership`."""
    user = UserModel(
        subject=subject,
        password_hash=password_hash,
        mfa_secret_encrypted=mfa_secret_encrypted,
    )
    session.add(user)
    session.flush()
    return user


# --- API keys (Phase 5 step 5) -----------------------------------------------


def create_api_key(
    session: Session,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    name: str,
    key_hash: str,
    created_by: uuid.UUID,
    expires_at: datetime,
) -> ApiKeyModel:
    """Ordinary RLS-scoped write, same convention as `create_invitation` --
    must run inside `access_session` as the creating (already-authenticated)
    admin. `user_id` is a freshly created service `User` row (see
    `api.repository.PostgresRepository.create_api_key`), not the admin's
    own -- `key_hash` is computed by the caller (`security.tokens.hash_token`);
    this function never sees the raw key."""
    api_key = ApiKeyModel(
        org_id=org_id,
        user_id=user_id,
        name=name,
        key_hash=key_hash,
        created_by=created_by,
        expires_at=expires_at,
    )
    session.add(api_key)
    session.flush()
    return api_key


def list_api_keys(
    session: Session, org_id: uuid.UUID, *, limit: int = 20, offset: int = 0
) -> tuple[list[ApiKeyModel], int]:
    """Every key on this org, active or not -- masking (never returning
    `key_hash`) and any "active only" filtering is an API-layer concern,
    same split as `list_org_memberships` deliberately returning revoked
    memberships too and letting callers decide."""
    total = session.execute(
        select(func.count()).select_from(ApiKeyModel).where(ApiKeyModel.org_id == org_id)
    ).scalar_one()
    rows = (
        session.execute(
            select(ApiKeyModel)
            .where(ApiKeyModel.org_id == org_id)
            .order_by(ApiKeyModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total


@dataclass(frozen=True, slots=True)
class ApiKeyAuthLookup:
    """What `get_api_key_by_hash` (the `SECURITY DEFINER` function,
    `alembic/versions/0009_api_key_lookup_function.py`) returns -- an
    anonymous, hash-authorized read, so this runs on a plain session, not
    `access_session` (there is no `user_id` yet; see that function's own
    docstring for why it bypasses RLS by design)."""

    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    revoked_at: datetime | None
    expires_at: datetime


def get_api_key_by_hash(session: Session, key_hash: str) -> ApiKeyAuthLookup | None:
    row = (
        session.execute(
            text(
                "SELECT id, org_id, user_id, name, revoked_at, expires_at "
                "FROM get_api_key_by_hash(:key_hash)"
            ),
            {"key_hash": key_hash},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return ApiKeyAuthLookup(**row)


def revoke_api_key(session: Session, api_key_id: uuid.UUID) -> bool:
    """Offboarding's API-key counterpart -- same soft-revoke-via-UPDATE
    shape as `revoke_membership`, gated by the same `org_access` RLS
    policy an org-resolved admin already has on `api_keys` (0007)."""
    result = session.execute(
        update(ApiKeyModel)
        .where(ApiKeyModel.id == api_key_id, ApiKeyModel.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    return result.rowcount > 0


def touch_api_key_last_used(session: Session, api_key_id: uuid.UUID) -> None:
    """Best-effort bookkeeping, called from the authenticated (already
    resolved-to-a-user_id) side of api-key auth, inside `access_session`
    scoped to that key's own service user -- `org_access`'s UPDATE grant
    covers it because that user's own membership resolves access to
    `api_key.org_id`. Not gated on `revoked_at`/`expires_at` -- by the time
    this is called, `api/auth.py` has already rejected a revoked or
    expired key, so this only ever runs for a key that just successfully
    authenticated."""
    session.execute(
        update(ApiKeyModel).where(ApiKeyModel.id == api_key_id).values(last_used_at=func.now())
    )


# --- Effective-dated contracts --------------------------------------------------


def create_contract(
    session: Session, org_id: uuid.UUID, payer_id: str, name: str
) -> ContractModel:
    contract = ContractModel(org_id=org_id, payer_id=payer_id, name=name)
    session.add(contract)
    session.flush()
    return contract


def get_contract_by_payer_id(
    session: Session, org_id: uuid.UUID, payer_id: str
) -> ContractModel | None:
    """For Phase 7's `timely_filing_days`/`packet_template` -- both live on
    `Contract`, not `ContractVersion` (see that model's docstring)."""
    return session.execute(
        select(ContractModel).where(
            ContractModel.org_id == org_id, ContractModel.payer_id == payer_id
        )
    ).scalar_one_or_none()


def create_contract_version(
    session: Session,
    org_id: uuid.UUID,
    contract_id: uuid.UUID,
    version: ContractVersion,
) -> ContractVersionModel:
    row = ContractVersionModel(
        org_id=org_id,
        contract_id=contract_id,
        effective_from=version.effective_from,
        effective_to=version.effective_to,
        default_pricing_method=version.default_pricing_method.value,
        percent_of_charge_rate=(
            version.percent_of_charge_rate.as_decimal()
            if version.percent_of_charge_rate is not None
            else None
        ),
        mppr_rule=_mppr_rule_to_json(version.mppr_rule),
        bilateral_rule=_bilateral_rule_to_json(version.bilateral_rule),
        assistant_surgeon_rule=_assistant_surgeon_rule_to_json(version.assistant_surgeon_rule),
        implant_carveout_rule=_implant_carveout_rule_to_json(version.implant_carveout_rule),
        case_rate_groups=_case_rate_groups_to_json(version.case_rate_groups),
    )
    session.add(row)
    session.flush()
    for procedure_code, amount in version.fee_schedule.items():
        session.add(
            FeeScheduleLineModel(
                org_id=org_id,
                contract_version_id=row.id,
                procedure_code=procedure_code,
                allowed_amount=amount.as_decimal(),
            )
        )
    session.flush()
    return row


def get_effective_contract_version(
    session: Session, org_id: uuid.UUID, payer_id: str, date_of_service: date
) -> ContractVersion | None:
    """Loads every version of `payer_id`'s contract for this org and
    hands off to the already-tested domain.contract.find_effective_contract
    -- Phase 3 adds no new date-effective logic, it only proves the Phase 1
    logic still picks the right version when the data comes from Postgres."""
    rows = (
        session.execute(
            select(ContractVersionModel)
            .join(ContractModel, ContractVersionModel.contract_id == ContractModel.id)
            .where(ContractModel.org_id == org_id, ContractModel.payer_id == payer_id)
            .order_by(ContractVersionModel.effective_from.desc())
        )
        .scalars()
        .all()
    )
    versions = [_contract_version_to_domain(session, row, payer_id) for row in rows]
    return find_effective_contract(payer_id, date_of_service, versions)


def list_contract_versions(
    session: Session, org_id: uuid.UUID, payer_id: str
) -> list[tuple[uuid.UUID, ContractVersion]]:
    """Every version of `payer_id`'s contract for this org, undated --
    callers that need to price several claims against possibly-different
    dates of service (ingestion) load once and pick per-claim via
    domain.contract.find_effective_contract themselves, rather than paying
    for a fresh query per claim. Returned paired with each version's row id
    since findings need to record which contract_version priced them
    (CLAUDE.md rule 8 / docs/PHASES.md: reproducible after a rules change),
    and the domain ContractVersion dataclass itself deliberately carries no
    DB identity."""
    rows = (
        session.execute(
            select(ContractVersionModel)
            .join(ContractModel, ContractVersionModel.contract_id == ContractModel.id)
            .where(ContractModel.org_id == org_id, ContractModel.payer_id == payer_id)
            .order_by(ContractVersionModel.effective_from.desc())
        )
        .scalars()
        .all()
    )
    return [(row.id, _contract_version_to_domain(session, row, payer_id)) for row in rows]


def list_contracts(
    session: Session, org_id: uuid.UUID, *, limit: int = 20, offset: int = 0
) -> tuple[list[ContractModel], int]:
    """All contracts for this org across every payer -- unlike
    `list_contract_versions`/`get_effective_contract_version`, which are
    scoped to one payer at a time for pricing lookups. For contract
    management's list view."""
    total = session.execute(
        select(func.count()).select_from(ContractModel).where(ContractModel.org_id == org_id)
    ).scalar_one()
    rows = (
        session.execute(
            select(ContractModel)
            .where(ContractModel.org_id == org_id)
            .order_by(ContractModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total


# --- Idempotent remittances -----------------------------------------------------


def record_remittance_if_new(
    session: Session,
    facility_id: uuid.UUID,
    file_hash: str,
    *,
    source: str,
    uploaded_by: str,
) -> tuple[RemittanceModel, bool]:
    """Returns (row, is_new). `is_new is False` means this exact file was
    already ingested for this facility -- the caller must not create any
    new claims or findings from it."""
    stmt = (
        pg_insert(RemittanceModel)
        .values(
            facility_id=facility_id,
            file_hash=file_hash,
            source=source,
            uploaded_by=uploaded_by,
            status="received",
        )
        .on_conflict_do_nothing(index_elements=["facility_id", "file_hash"])
        .returning(RemittanceModel.id)
    )
    inserted_id = session.execute(stmt).scalar_one_or_none()
    if inserted_id is not None:
        row = session.get(RemittanceModel, inserted_id)
        if row is None:
            raise RuntimeError(
                f"remittance {inserted_id} was just inserted but cannot be re-read "
                "in the same transaction -- this should never happen"
            )
        return row, True

    existing = session.execute(
        select(RemittanceModel).where(
            RemittanceModel.facility_id == facility_id, RemittanceModel.file_hash == file_hash
        )
    ).scalar_one()
    return existing, False


# --- Claims and findings ---------------------------------------------------------


def create_claim(
    session: Session,
    facility_id: uuid.UUID,
    remittance_id: uuid.UUID,
    *,
    patient_control_number: str,
    payer_claim_control_number: str,
    status: str,
    date_of_service: date,
    total_charge: Decimal,
    total_paid_reported: Decimal,
    patient_responsibility: Decimal,
    patient_name_encrypted: str | None = None,
    patient_member_id_encrypted: str | None = None,
) -> ClaimModel:
    # Callers pass already-encrypted values (security.phi_columns) -- this
    # layer only ever persists opaque strings, never plaintext PHI. See
    # ingestion.apply for the encryption call site.
    claim = ClaimModel(
        facility_id=facility_id,
        remittance_id=remittance_id,
        patient_control_number=patient_control_number,
        payer_claim_control_number=payer_claim_control_number,
        status=status,
        date_of_service=date_of_service,
        total_charge=total_charge,
        total_paid_reported=total_paid_reported,
        patient_responsibility=patient_responsibility,
        patient_name_encrypted=patient_name_encrypted,
        patient_member_id_encrypted=patient_member_id_encrypted,
    )
    session.add(claim)
    session.flush()
    return claim


def create_service_line(
    session: Session,
    facility_id: uuid.UUID,
    claim_id: uuid.UUID,
    *,
    line_index: int,
    procedure_code: str,
    modifiers: Sequence[str],
    revenue_code: str | None,
    charge: Decimal,
    allowed: Decimal,
    paid_computed: Decimal,
    service_date: date | None,
) -> ServiceLineModel:
    line = ServiceLineModel(
        facility_id=facility_id,
        claim_id=claim_id,
        line_index=line_index,
        procedure_code=procedure_code,
        modifiers=list(modifiers),
        revenue_code=revenue_code,
        charge=charge,
        allowed=allowed,
        paid_computed=paid_computed,
        service_date=service_date,
    )
    session.add(line)
    session.flush()
    return line


def create_adjustment(
    session: Session,
    facility_id: uuid.UUID,
    claim_id: uuid.UUID,
    service_line_id: uuid.UUID | None,
    *,
    group_code: str,
    reason_code: str,
    amount: Decimal,
) -> AdjustmentModel:
    adjustment = AdjustmentModel(
        facility_id=facility_id,
        claim_id=claim_id,
        service_line_id=service_line_id,
        group_code=group_code,
        reason_code=reason_code,
        amount=amount,
    )
    session.add(adjustment)
    session.flush()
    return adjustment


def update_remittance_status(
    session: Session,
    facility_id: uuid.UUID,
    remittance_id: uuid.UUID,
    *,
    status: str,
    quarantine_reason: str | None = None,
) -> RemittanceModel:
    row = session.execute(
        select(RemittanceModel).where(
            RemittanceModel.facility_id == facility_id, RemittanceModel.id == remittance_id
        )
    ).scalar_one()
    row.status = status
    row.quarantine_reason = quarantine_reason
    session.flush()
    return row


def save_findings(
    session: Session,
    facility_id: uuid.UUID,
    claim_id: uuid.UUID,
    service_line_ids: Mapping[int, uuid.UUID],
    contract_version_id: uuid.UUID | None,
    findings: Sequence[Finding],
) -> list[FindingModel]:
    rows: list[FindingModel] = []
    for finding in findings:
        # A reversal-netting finding carries its own service_line_id (the
        # ORIGINAL finding's, already persisted) and must never be looked up
        # by line_index against *this* claim's own lines -- see F-01 in
        # docs/audit/REGISTER.md. Only a regular (non-reversal) finding,
        # whose line_index always corresponds to this same claim's own
        # service lines, uses the index-based lookup.
        resolved_service_line_id = (
            finding.service_line_id
            if finding.service_line_id is not None
            else service_line_ids[finding.line_index]
        )
        row = FindingModel(
            facility_id=facility_id,
            claim_id=claim_id,
            service_line_id=resolved_service_line_id,
            contract_version_id=contract_version_id,
            line_index=finding.line_index,
            procedure_code=finding.procedure_code,
            expected_allowed=(
                finding.expected_allowed.as_decimal()
                if finding.expected_allowed is not None
                else None
            ),
            actual_allowed=finding.actual_allowed.as_decimal(),
            shortfall=finding.shortfall.as_decimal(),
            root_cause=finding.root_cause.name,
            evidence=finding.evidence,
            rule_version=RULES_VERSION,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def list_findings_by_payer_claim_control_number(
    session: Session, facility_id: uuid.UUID, payer_claim_control_number: str
) -> list[FindingModel]:
    """Findings belonging to any claim previously ingested under this payer
    claim control number -- used by ingestion to net a reversal (CLP02=22)
    against what it's reversing. Not scoped by remittance_id: a reversal
    typically arrives in a different file than the original payment.

    Explicitly filtered by facility_id (in addition to whatever RLS
    enforces on the connection) -- this was previously accepted-but-unused,
    meaning a payer_claim_control_number collision across facilities would
    leak findings across facilities on any connection where RLS wasn't
    already doing the job (e.g. a superuser/owner connection)."""
    return list(
        session.execute(
            select(FindingModel)
            .join(ClaimModel, FindingModel.claim_id == ClaimModel.id)
            .where(
                ClaimModel.facility_id == facility_id,
                ClaimModel.payer_claim_control_number == payer_claim_control_number,
            )
        )
        .scalars()
        .all()
    )


# --- Outcomes and confidence scoring (Phase 12) -----------------------------------


def record_finding_outcome(
    session: Session,
    facility_id: uuid.UUID,
    finding_id: uuid.UUID,
    *,
    outcome: str,
    amount_recovered: Decimal | None,
    recorded_by: str,
) -> FindingModel:
    """The human-recording step -- callers must call
    domain.outcomes.validate_outcome_recording first (this function does
    not re-check that an outcome isn't already set or that the finding
    isn't a CORRECT_NO_VARIANCE one; that's pure domain logic, not a
    database concern)."""
    finding = session.execute(
        select(FindingModel).where(
            FindingModel.facility_id == facility_id, FindingModel.id == finding_id
        )
    ).scalar_one()
    finding.outcome = outcome
    finding.amount_recovered = amount_recovered
    finding.outcome_recorded_by = recorded_by
    finding.outcome_recorded_at = datetime.now(UTC)
    session.flush()
    return finding


def list_historical_outcomes(
    session: Session, facility_id: uuid.UUID, payer_id: str, root_cause: str
) -> list[FindingModel]:
    """Every already-decided finding (outcome recorded) for this facility
    and root cause -- domain.outcomes.calculate_confidence turns this into
    a score for a new finding matching the same two dimensions. Payer
    identity comes via contract_version -> contract, since Claim itself
    doesn't carry payer_id directly; findings with no contract_version
    (UNPRICED_CODE) can never match a payer this way and are correctly
    excluded."""
    return list(
        session.execute(
            select(FindingModel)
            .join(
                ContractVersionModel,
                FindingModel.contract_version_id == ContractVersionModel.id,
            )
            .join(ContractModel, ContractVersionModel.contract_id == ContractModel.id)
            .where(
                FindingModel.facility_id == facility_id,
                ContractModel.payer_id == payer_id,
                FindingModel.root_cause == root_cause,
                FindingModel.outcome.is_not(None),
            )
        )
        .scalars()
        .all()
    )


def list_findings_past_deadline_without_outcome(
    session: Session, facility_id: uuid.UUID, as_of: date
) -> list[FindingModel]:
    """Findings whose approved recovery packet's appeal deadline has
    passed with no outcome recorded yet. A human confirms these as
    `expired` (domain.outcomes.Outcome.EXPIRED) -- this query only
    surfaces candidates, it never writes anything itself."""
    return list(
        session.execute(
            select(FindingModel)
            .join(RecoveryPacketModel, RecoveryPacketModel.finding_id == FindingModel.id)
            .where(
                FindingModel.facility_id == facility_id,
                FindingModel.outcome.is_(None),
                RecoveryPacketModel.status == "approved",
                RecoveryPacketModel.deadline < as_of,
            )
        )
        .scalars()
        .all()
    )


@dataclass(frozen=True, slots=True)
class FindingDetail:
    """Full evidence chain for a single finding -- Finding joined with its
    Claim, ServiceLine, and every Adjustment on that claim (claim-level and
    line-level). Repository functions elsewhere in this module return bare
    ORM rows; this one is a small dataclass because a finding detail
    response is inherently a join of four tables, not one row."""

    finding: FindingModel
    claim: ClaimModel
    service_line: ServiceLineModel
    adjustments: list[AdjustmentModel]


def list_findings(
    session: Session,
    facility_id: uuid.UUID,
    *,
    root_cause: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    min_shortfall: Decimal | None = None,
    remittance_id: uuid.UUID | None = None,
    claim_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[FindingModel], int]:
    """Filters never include a patient identifier -- callers (the API
    layer) must not expose one as a query parameter, since query strings
    land in access logs (CLAUDE.md rule 6)."""
    conditions = [FindingModel.facility_id == facility_id]
    if root_cause is not None:
        conditions.append(FindingModel.root_cause == root_cause)
    if min_shortfall is not None:
        conditions.append(FindingModel.shortfall >= min_shortfall)
    if claim_id is not None:
        conditions.append(FindingModel.claim_id == claim_id)
    if date_from is not None or date_to is not None or remittance_id is not None:
        query_base = select(FindingModel).join(ClaimModel, FindingModel.claim_id == ClaimModel.id)
        count_base = (
            select(func.count())
            .select_from(FindingModel)
            .join(ClaimModel, FindingModel.claim_id == ClaimModel.id)
        )
        if date_from is not None:
            conditions.append(ClaimModel.date_of_service >= date_from)
        if date_to is not None:
            conditions.append(ClaimModel.date_of_service <= date_to)
        if remittance_id is not None:
            conditions.append(ClaimModel.remittance_id == remittance_id)
    else:
        query_base = select(FindingModel)
        count_base = select(func.count()).select_from(FindingModel)

    total = session.execute(count_base.where(*conditions)).scalar_one()
    rows = (
        session.execute(
            query_base.where(*conditions)
            .order_by(FindingModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def get_finding_detail(
    session: Session, facility_id: uuid.UUID, finding_id: uuid.UUID
) -> FindingDetail | None:
    finding = session.execute(
        select(FindingModel).where(
            FindingModel.facility_id == facility_id, FindingModel.id == finding_id
        )
    ).scalar_one_or_none()
    if finding is None:
        return None
    claim = session.get(ClaimModel, finding.claim_id)
    service_line = session.get(ServiceLineModel, finding.service_line_id)
    if claim is None or service_line is None:
        raise RuntimeError(
            f"finding {finding_id} references a claim or service_line that no "
            "longer exists -- FK constraints should make this impossible"
        )
    adjustments = list(
        session.execute(
            select(AdjustmentModel).where(AdjustmentModel.claim_id == finding.claim_id)
        )
        .scalars()
        .all()
    )
    return FindingDetail(
        finding=finding, claim=claim, service_line=service_line, adjustments=adjustments
    )


# --- Audit log -------------------------------------------------------------------


def write_audit_log(
    session: Session,
    facility_id: uuid.UUID,
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    phi_accessed: bool = False,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> AuditLogModel:
    entry = AuditLogModel(
        facility_id=facility_id,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        phi_accessed=phi_accessed,
        source_ip=source_ip,
        request_id=request_id,
    )
    session.add(entry)
    session.flush()
    return entry


def list_audit_log(
    session: Session,
    facility_id: uuid.UUID,
    *,
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[AuditLogModel], int]:
    conditions = [AuditLogModel.facility_id == facility_id]
    if actor is not None:
        conditions.append(AuditLogModel.actor == actor)
    if action is not None:
        conditions.append(AuditLogModel.action == action)
    if resource_type is not None:
        conditions.append(AuditLogModel.resource_type == resource_type)
    if date_from is not None:
        conditions.append(AuditLogModel.occurred_at >= date_from)
    if date_to is not None:
        conditions.append(AuditLogModel.occurred_at <= date_to)

    total = session.execute(
        select(func.count()).select_from(AuditLogModel).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            select(AuditLogModel)
            .where(*conditions)
            .order_by(AuditLogModel.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total


# --- Recovery packets (Phase 7) --------------------------------------------------


def create_recovery_packet(
    session: Session,
    facility_id: uuid.UUID,
    finding_id: uuid.UUID,
    *,
    draft_text: str,
    deadline: date,
    generated_by: str,
) -> RecoveryPacketModel:
    packet = RecoveryPacketModel(
        facility_id=facility_id,
        finding_id=finding_id,
        status="draft",
        draft_text=draft_text,
        deadline=deadline,
        generated_by=generated_by,
    )
    session.add(packet)
    session.flush()
    return packet


def decide_recovery_packet(
    session: Session,
    facility_id: uuid.UUID,
    packet_id: uuid.UUID,
    *,
    approve: bool,
    decided_by: str,
) -> RecoveryPacketModel:
    """The human-approval step -- never automatic. `approve=False` records
    a rejection, not a deletion; the draft and who declined it stay on
    the record."""
    packet = session.execute(
        select(RecoveryPacketModel).where(
            RecoveryPacketModel.facility_id == facility_id, RecoveryPacketModel.id == packet_id
        )
    ).scalar_one()
    packet.status = "approved" if approve else "rejected"
    packet.decided_by = decided_by
    packet.decided_at = datetime.now(UTC)
    session.flush()
    return packet


def list_recovery_packets_for_finding(
    session: Session, facility_id: uuid.UUID, finding_id: uuid.UUID
) -> list[RecoveryPacketModel]:
    return list(
        session.execute(
            select(RecoveryPacketModel)
            .where(
                RecoveryPacketModel.facility_id == facility_id,
                RecoveryPacketModel.finding_id == finding_id,
            )
            .order_by(RecoveryPacketModel.generated_at.desc())
        )
        .scalars()
        .all()
    )


# --- PHI access log and the auditor-facing claim access history (Phase 8) ------


def write_phi_access_log(
    session: Session, facility_id: uuid.UUID, *, actor: str, claim_id: uuid.UUID, purpose: str
) -> PHIAccessLogModel:
    entry = PHIAccessLogModel(
        facility_id=facility_id, actor=actor, claim_id=claim_id, purpose=purpose
    )
    session.add(entry)
    session.flush()
    return entry


def list_audit_log_by_resource_ids(
    session: Session, facility_id: uuid.UUID, resource_type: str, resource_ids: Sequence[str]
) -> list[AuditLogModel]:
    if not resource_ids:
        return []
    return list(
        session.execute(
            select(AuditLogModel).where(
                AuditLogModel.facility_id == facility_id,
                AuditLogModel.resource_type == resource_type,
                AuditLogModel.resource_id.in_(resource_ids),
            )
        )
        .scalars()
        .all()
    )


def get_claim_access_history(
    session: Session, facility_id: uuid.UUID, claim_id: uuid.UUID
) -> tuple[AccessEvent, ...]:
    """Auditor-facing report: "who accessed this claim's data, when, and
    why." A claim's findings and recovery packets reference it only
    indirectly, so this resolves their ids first, gathers `audit_log`
    entries across all three resource types plus every `phi_access_log`
    entry, converts each row to a plain `AccessEvent`, and hands off to
    the pure `merge_access_history` for the chronological merge."""
    finding_ids = list(
        session.execute(
            select(FindingModel.id).where(
                FindingModel.facility_id == facility_id, FindingModel.claim_id == claim_id
            )
        )
        .scalars()
        .all()
    )
    packet_ids = (
        list(
            session.execute(
                select(RecoveryPacketModel.id).where(
                    RecoveryPacketModel.facility_id == facility_id,
                    RecoveryPacketModel.finding_id.in_(finding_ids),
                )
            )
            .scalars()
            .all()
        )
        if finding_ids
        else []
    )

    audit_rows = (
        list_audit_log_by_resource_ids(session, facility_id, "claim", [str(claim_id)])
        + list_audit_log_by_resource_ids(
            session, facility_id, "finding", [str(fid) for fid in finding_ids]
        )
        + list_audit_log_by_resource_ids(
            session, facility_id, "recovery_packet", [str(pid) for pid in packet_ids]
        )
    )
    phi_rows = list(
        session.execute(
            select(PHIAccessLogModel).where(
                PHIAccessLogModel.facility_id == facility_id,
                PHIAccessLogModel.claim_id == claim_id,
            )
        )
        .scalars()
        .all()
    )

    audit_events = [
        AccessEvent(
            occurred_at=row.occurred_at,
            actor=row.actor,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            purpose=None,
        )
        for row in audit_rows
    ]
    phi_events = [
        AccessEvent(
            occurred_at=row.accessed_at,
            actor=row.actor,
            action="phi_access",
            resource_type="claim",
            resource_id=str(row.claim_id),
            purpose=row.purpose,
        )
        for row in phi_rows
    ]
    return merge_access_history(audit_events, phi_events)
