"""Role-based access control (Phase 4, `docs/MASTER-BUILD-PROMPT-V2.md`).
See `docs/PERMISSIONS.md` for the full matrix in table form.

`can(role, action)` is a deny-by-default lookup table, not an if/elif
chain -- an accidentally-omitted (role, action) pair fails closed rather
than silently allowing.

Scope boundary: this module answers "can this role perform this kind of
action at all." It does not implement *which records* -- that's resolved
access (`organizations`/`facilities`/`memberships`, RLS via
`resolve_accessible_facility_ids`/`resolve_accessible_org_ids` in
`alembic/versions/0001_initial_schema.py`). `platform_admin` and
`org_admin` deliberately hold the *same* action-level permissions here --
what actually differs between them is where their `Membership` row sits
in the org hierarchy, which determines which orgs/facilities the access
resolution functions return, not what actions they're allowed to attempt.
This module also does not implement row-level worklist assignment scoping
("a biller sees only *their assigned* worklists") -- that requires an
assignment data model that doesn't exist until a later phase builds the
worklist feature; row-level filtering belongs at that query layer, on top
of the action-level check made here.
"""

from __future__ import annotations

import enum


class Role(enum.Enum):
    PLATFORM_ADMIN = "platform_admin"
    ORG_ADMIN = "org_admin"
    MANAGER = "manager"
    BILLER = "biller"
    ANALYST = "analyst"
    AUDITOR = "auditor"
    API_SERVICE = "api_service"


class Action(enum.Enum):
    READ_CLAIM = "read_claim"
    READ_FINDING = "read_finding"
    READ_WORKLIST = "read_worklist"
    EXPORT_WORKLIST = "export_worklist"
    UPLOAD_REMITTANCE = "upload_remittance"
    READ_CONTRACT = "read_contract"
    MANAGE_CONTRACT = "manage_contract"
    DRAFT_RECOVERY_PACKET = "draft_recovery_packet"
    APPROVE_RECOVERY_PACKET = "approve_recovery_packet"
    MANAGE_USERS = "manage_users"
    READ_AUDIT_LOG = "read_audit_log"
    READ_PHI_ACCESS_LOG = "read_phi_access_log"
    RECORD_FINDING_OUTCOME = "record_finding_outcome"
    # Field-level PHI masking gate (security/phi_masking.py) -- lacking
    # this action doesn't block reading a claim/finding at all, it means
    # the two PHI fields on that response (patient name, member id) come
    # back masked instead of the endpoint being denied outright.
    VIEW_UNMASKED_PHI = "view_unmasked_phi"


# Every action a biller needs day to day, short of contract/user
# management and unmasked-PHI-only-for-compliance concerns -- the
# baseline every non-admin operational role builds on.
_BILLER_ACTIONS = frozenset(
    {
        Action.READ_CLAIM,
        Action.READ_FINDING,
        Action.READ_WORKLIST,
        Action.EXPORT_WORKLIST,
        Action.UPLOAD_REMITTANCE,
        Action.READ_CONTRACT,
        Action.DRAFT_RECOVERY_PACKET,
        Action.APPROVE_RECOVERY_PACKET,
        Action.RECORD_FINDING_OUTCOME,
        Action.VIEW_UNMASKED_PHI,
    }
)

_PERMISSIONS: dict[Role, frozenset[Action]] = {
    # platform_admin and org_admin are intentionally identical here -- see
    # the module docstring for why the distinction is hierarchy position
    # (resolved access), not a different action set.
    Role.PLATFORM_ADMIN: frozenset(Action),
    Role.ORG_ADMIN: frozenset(Action),
    # A biller's full action set, plus oversight reads (audit log, PHI
    # access log) that an individual biller doesn't need but whoever
    # supervises a facility's billers does.
    Role.MANAGER: _BILLER_ACTIONS
    | frozenset({Action.READ_AUDIT_LOG, Action.READ_PHI_ACCESS_LOG}),
    Role.BILLER: _BILLER_ACTIONS,
    # The role field-level PHI masking exists for: full read access to
    # amounts/codes/worklists, but no VIEW_UNMASKED_PHI -- patient name
    # and member id come back masked (security/phi_masking.py).
    Role.ANALYST: frozenset(
        {
            Action.READ_CLAIM,
            Action.READ_FINDING,
            Action.READ_WORKLIST,
            Action.READ_CONTRACT,
        }
    ),
    Role.AUDITOR: frozenset(
        {
            Action.READ_CLAIM,
            Action.READ_FINDING,
            Action.READ_CONTRACT,
            Action.READ_AUDIT_LOG,
            Action.READ_PHI_ACCESS_LOG,
            Action.VIEW_UNMASKED_PHI,
        }
    ),
    # Machine-to-machine (API keys, Phase 5) -- narrow and read/upload
    # only. No packet drafting/approval (needs human judgment), no
    # user/contract management, no audit/PHI-access-log reads, and no
    # unmasked PHI by default: least privilege for an unattended credential
    # until a real integration is shown to need more.
    Role.API_SERVICE: frozenset(
        {
            Action.READ_CLAIM,
            Action.READ_FINDING,
            Action.READ_WORKLIST,
            Action.READ_CONTRACT,
            Action.UPLOAD_REMITTANCE,
        }
    ),
}


def can(role: Role, action: Action) -> bool:
    return action in _PERMISSIONS.get(role, frozenset())
