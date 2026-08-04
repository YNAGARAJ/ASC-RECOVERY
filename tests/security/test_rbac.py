"""Tests the full Role x Action permission matrix, not a sample -- the
expected table here is authored independently of security.rbac's
`_PERMISSIONS` dict, so this actually verifies the matrix rather than
re-asserting it against itself.
"""

from __future__ import annotations

import itertools

import pytest

from security.rbac import Action, Role, can

_EXPECTED: dict[tuple[Role, Action], bool] = {
    (Role.VIEWER, Action.READ_CLAIM): True,
    (Role.VIEWER, Action.READ_FINDING): True,
    (Role.VIEWER, Action.READ_WORKLIST): True,
    (Role.VIEWER, Action.EXPORT_WORKLIST): False,
    (Role.VIEWER, Action.UPLOAD_REMITTANCE): False,
    (Role.VIEWER, Action.READ_CONTRACT): True,
    (Role.VIEWER, Action.MANAGE_CONTRACT): False,
    (Role.VIEWER, Action.APPROVE_RECOVERY_PACKET): False,
    (Role.VIEWER, Action.MANAGE_USERS): False,
    (Role.VIEWER, Action.READ_AUDIT_LOG): False,
    (Role.VIEWER, Action.READ_PHI_ACCESS_LOG): False,
    (Role.BILLER, Action.READ_CLAIM): True,
    (Role.BILLER, Action.READ_FINDING): True,
    (Role.BILLER, Action.READ_WORKLIST): True,
    (Role.BILLER, Action.EXPORT_WORKLIST): True,
    (Role.BILLER, Action.UPLOAD_REMITTANCE): True,
    (Role.BILLER, Action.READ_CONTRACT): True,
    (Role.BILLER, Action.MANAGE_CONTRACT): False,
    (Role.BILLER, Action.APPROVE_RECOVERY_PACKET): True,
    (Role.BILLER, Action.MANAGE_USERS): False,
    (Role.BILLER, Action.READ_AUDIT_LOG): False,
    (Role.BILLER, Action.READ_PHI_ACCESS_LOG): False,
    (Role.ADMIN, Action.READ_CLAIM): True,
    (Role.ADMIN, Action.READ_FINDING): True,
    (Role.ADMIN, Action.READ_WORKLIST): True,
    (Role.ADMIN, Action.EXPORT_WORKLIST): True,
    (Role.ADMIN, Action.UPLOAD_REMITTANCE): True,
    (Role.ADMIN, Action.READ_CONTRACT): True,
    (Role.ADMIN, Action.MANAGE_CONTRACT): True,
    (Role.ADMIN, Action.APPROVE_RECOVERY_PACKET): True,
    (Role.ADMIN, Action.MANAGE_USERS): True,
    (Role.ADMIN, Action.READ_AUDIT_LOG): True,
    (Role.ADMIN, Action.READ_PHI_ACCESS_LOG): True,
    (Role.AUDITOR, Action.READ_CLAIM): True,
    (Role.AUDITOR, Action.READ_FINDING): True,
    (Role.AUDITOR, Action.READ_WORKLIST): False,
    (Role.AUDITOR, Action.EXPORT_WORKLIST): False,
    (Role.AUDITOR, Action.UPLOAD_REMITTANCE): False,
    (Role.AUDITOR, Action.READ_CONTRACT): True,
    (Role.AUDITOR, Action.MANAGE_CONTRACT): False,
    (Role.AUDITOR, Action.APPROVE_RECOVERY_PACKET): False,
    (Role.AUDITOR, Action.MANAGE_USERS): False,
    (Role.AUDITOR, Action.READ_AUDIT_LOG): True,
    (Role.AUDITOR, Action.READ_PHI_ACCESS_LOG): True,
}


def test_expected_matrix_covers_every_role_action_pair() -> None:
    """Guards against the expected table itself silently going stale if a
    Role or Action is added later without updating this file."""
    assert set(_EXPECTED.keys()) == set(itertools.product(Role, Action))


@pytest.mark.parametrize("role,action", list(itertools.product(Role, Action)))
def test_full_permission_matrix(role: Role, action: Action) -> None:
    assert can(role, action) == _EXPECTED[(role, action)]


def test_admin_has_every_action() -> None:
    assert all(can(Role.ADMIN, action) for action in Action)


def test_auditor_cannot_export_or_manage_anything() -> None:
    assert not can(Role.AUDITOR, Action.EXPORT_WORKLIST)
    assert not can(Role.AUDITOR, Action.MANAGE_CONTRACT)
    assert not can(Role.AUDITOR, Action.MANAGE_USERS)
    assert not can(Role.AUDITOR, Action.UPLOAD_REMITTANCE)
