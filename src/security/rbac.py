"""Role-based access control. Roles: viewer, biller, admin, auditor, per
the phase prompt.

`can(role, action)` is a deny-by-default lookup table, not an if/elif
chain -- an accidentally-omitted (role, action) pair fails closed rather
than silently allowing.

Scope boundary: this module answers "can this role perform this kind of
action at all." It does not implement row-level scoping (e.g. "a biller
sees only *their assigned* worklists") -- that requires a worklist/
assignment data model that doesn't exist until Phase 6/7 build the API and
worklist features; row-level filtering belongs at that query layer, on top
of the action-level check made here.
"""

from __future__ import annotations

import enum


class Role(enum.Enum):
    VIEWER = "viewer"
    BILLER = "biller"
    ADMIN = "admin"
    AUDITOR = "auditor"


class Action(enum.Enum):
    READ_CLAIM = "read_claim"
    READ_FINDING = "read_finding"
    READ_WORKLIST = "read_worklist"
    EXPORT_WORKLIST = "export_worklist"
    UPLOAD_REMITTANCE = "upload_remittance"
    READ_CONTRACT = "read_contract"
    MANAGE_CONTRACT = "manage_contract"
    APPROVE_RECOVERY_PACKET = "approve_recovery_packet"
    MANAGE_USERS = "manage_users"
    READ_AUDIT_LOG = "read_audit_log"
    READ_PHI_ACCESS_LOG = "read_phi_access_log"


_PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.VIEWER: frozenset(
        {
            Action.READ_CLAIM,
            Action.READ_FINDING,
            Action.READ_WORKLIST,
            Action.READ_CONTRACT,
        }
    ),
    Role.BILLER: frozenset(
        {
            Action.READ_CLAIM,
            Action.READ_FINDING,
            Action.READ_WORKLIST,
            Action.EXPORT_WORKLIST,
            Action.UPLOAD_REMITTANCE,
            Action.READ_CONTRACT,
            Action.APPROVE_RECOVERY_PACKET,
        }
    ),
    Role.ADMIN: frozenset(Action),
    Role.AUDITOR: frozenset(
        {
            Action.READ_CLAIM,
            Action.READ_FINDING,
            Action.READ_CONTRACT,
            Action.READ_AUDIT_LOG,
            Action.READ_PHI_ACCESS_LOG,
        }
    ),
}


def can(role: Role, action: Action) -> bool:
    return action in _PERMISSIONS.get(role, frozenset())
