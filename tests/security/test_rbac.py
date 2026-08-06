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
    # platform_admin and org_admin: every action, deliberately identical
    # (see security/rbac.py's module docstring -- resolved access, not
    # this table, is what actually distinguishes them).
    **{(Role.PLATFORM_ADMIN, action): True for action in Action},
    **{(Role.ORG_ADMIN, action): True for action in Action},
    (Role.MANAGER, Action.READ_CLAIM): True,
    (Role.MANAGER, Action.READ_FINDING): True,
    (Role.MANAGER, Action.READ_WORKLIST): True,
    (Role.MANAGER, Action.EXPORT_WORKLIST): True,
    (Role.MANAGER, Action.UPLOAD_REMITTANCE): True,
    (Role.MANAGER, Action.READ_CONTRACT): True,
    (Role.MANAGER, Action.MANAGE_CONTRACT): False,
    (Role.MANAGER, Action.DRAFT_RECOVERY_PACKET): True,
    (Role.MANAGER, Action.APPROVE_RECOVERY_PACKET): True,
    (Role.MANAGER, Action.MANAGE_USERS): False,
    (Role.MANAGER, Action.READ_AUDIT_LOG): True,
    (Role.MANAGER, Action.READ_PHI_ACCESS_LOG): True,
    (Role.MANAGER, Action.RECORD_FINDING_OUTCOME): True,
    (Role.MANAGER, Action.VIEW_UNMASKED_PHI): True,
    (Role.BILLER, Action.READ_CLAIM): True,
    (Role.BILLER, Action.READ_FINDING): True,
    (Role.BILLER, Action.READ_WORKLIST): True,
    (Role.BILLER, Action.EXPORT_WORKLIST): True,
    (Role.BILLER, Action.UPLOAD_REMITTANCE): True,
    (Role.BILLER, Action.READ_CONTRACT): True,
    (Role.BILLER, Action.MANAGE_CONTRACT): False,
    (Role.BILLER, Action.DRAFT_RECOVERY_PACKET): True,
    (Role.BILLER, Action.APPROVE_RECOVERY_PACKET): True,
    (Role.BILLER, Action.MANAGE_USERS): False,
    (Role.BILLER, Action.READ_AUDIT_LOG): False,
    (Role.BILLER, Action.READ_PHI_ACCESS_LOG): False,
    (Role.BILLER, Action.RECORD_FINDING_OUTCOME): True,
    (Role.BILLER, Action.VIEW_UNMASKED_PHI): True,
    (Role.ANALYST, Action.READ_CLAIM): True,
    (Role.ANALYST, Action.READ_FINDING): True,
    (Role.ANALYST, Action.READ_WORKLIST): True,
    (Role.ANALYST, Action.EXPORT_WORKLIST): False,
    (Role.ANALYST, Action.UPLOAD_REMITTANCE): False,
    (Role.ANALYST, Action.READ_CONTRACT): True,
    (Role.ANALYST, Action.MANAGE_CONTRACT): False,
    (Role.ANALYST, Action.DRAFT_RECOVERY_PACKET): False,
    (Role.ANALYST, Action.APPROVE_RECOVERY_PACKET): False,
    (Role.ANALYST, Action.MANAGE_USERS): False,
    (Role.ANALYST, Action.READ_AUDIT_LOG): False,
    (Role.ANALYST, Action.READ_PHI_ACCESS_LOG): False,
    (Role.ANALYST, Action.RECORD_FINDING_OUTCOME): False,
    (Role.ANALYST, Action.VIEW_UNMASKED_PHI): False,
    (Role.AUDITOR, Action.READ_CLAIM): True,
    (Role.AUDITOR, Action.READ_FINDING): True,
    (Role.AUDITOR, Action.READ_WORKLIST): False,
    (Role.AUDITOR, Action.EXPORT_WORKLIST): False,
    (Role.AUDITOR, Action.UPLOAD_REMITTANCE): False,
    (Role.AUDITOR, Action.READ_CONTRACT): True,
    (Role.AUDITOR, Action.MANAGE_CONTRACT): False,
    (Role.AUDITOR, Action.DRAFT_RECOVERY_PACKET): False,
    (Role.AUDITOR, Action.APPROVE_RECOVERY_PACKET): False,
    (Role.AUDITOR, Action.MANAGE_USERS): False,
    (Role.AUDITOR, Action.READ_AUDIT_LOG): True,
    (Role.AUDITOR, Action.READ_PHI_ACCESS_LOG): True,
    (Role.AUDITOR, Action.RECORD_FINDING_OUTCOME): False,
    (Role.AUDITOR, Action.VIEW_UNMASKED_PHI): True,
    (Role.API_SERVICE, Action.READ_CLAIM): True,
    (Role.API_SERVICE, Action.READ_FINDING): True,
    (Role.API_SERVICE, Action.READ_WORKLIST): True,
    (Role.API_SERVICE, Action.EXPORT_WORKLIST): False,
    (Role.API_SERVICE, Action.UPLOAD_REMITTANCE): True,
    (Role.API_SERVICE, Action.READ_CONTRACT): True,
    (Role.API_SERVICE, Action.MANAGE_CONTRACT): False,
    (Role.API_SERVICE, Action.DRAFT_RECOVERY_PACKET): False,
    (Role.API_SERVICE, Action.APPROVE_RECOVERY_PACKET): False,
    (Role.API_SERVICE, Action.MANAGE_USERS): False,
    (Role.API_SERVICE, Action.READ_AUDIT_LOG): False,
    (Role.API_SERVICE, Action.READ_PHI_ACCESS_LOG): False,
    (Role.API_SERVICE, Action.RECORD_FINDING_OUTCOME): False,
    (Role.API_SERVICE, Action.VIEW_UNMASKED_PHI): False,
}


def test_expected_matrix_covers_every_role_action_pair() -> None:
    """Guards against the expected table itself silently going stale if a
    Role or Action is added later without updating this file."""
    assert set(_EXPECTED.keys()) == set(itertools.product(Role, Action))


@pytest.mark.parametrize("role,action", list(itertools.product(Role, Action)))
def test_full_permission_matrix(role: Role, action: Action) -> None:
    assert can(role, action) == _EXPECTED[(role, action)]


def test_platform_admin_and_org_admin_have_every_action() -> None:
    assert all(can(Role.PLATFORM_ADMIN, action) for action in Action)
    assert all(can(Role.ORG_ADMIN, action) for action in Action)


def test_auditor_cannot_export_or_manage_anything() -> None:
    assert not can(Role.AUDITOR, Action.EXPORT_WORKLIST)
    assert not can(Role.AUDITOR, Action.MANAGE_CONTRACT)
    assert not can(Role.AUDITOR, Action.MANAGE_USERS)
    assert not can(Role.AUDITOR, Action.UPLOAD_REMITTANCE)


def test_analyst_never_views_unmasked_phi() -> None:
    assert not can(Role.ANALYST, Action.VIEW_UNMASKED_PHI)


def test_api_service_cannot_draft_or_approve_packets_or_manage_anything() -> None:
    assert not can(Role.API_SERVICE, Action.DRAFT_RECOVERY_PACKET)
    assert not can(Role.API_SERVICE, Action.APPROVE_RECOVERY_PACKET)
    assert not can(Role.API_SERVICE, Action.MANAGE_CONTRACT)
    assert not can(Role.API_SERVICE, Action.MANAGE_USERS)
    assert not can(Role.API_SERVICE, Action.VIEW_UNMASKED_PHI)
