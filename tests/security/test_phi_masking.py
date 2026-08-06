"""Tests for field-level PHI masking (Phase 4, docs/MASTER-BUILD-PROMPT-V2.md)."""

from __future__ import annotations

import pytest

from security.phi_masking import mask_patient_fields, mask_phi_value
from security.rbac import Role


@pytest.mark.parametrize(
    "role",
    [Role.PLATFORM_ADMIN, Role.ORG_ADMIN, Role.MANAGER, Role.BILLER, Role.AUDITOR],
)
def test_roles_with_view_unmasked_phi_see_the_real_value(role: Role) -> None:
    assert mask_phi_value(role, "Jane Doe") == "Jane Doe"


@pytest.mark.parametrize("role", [Role.ANALYST, Role.API_SERVICE])
def test_roles_without_view_unmasked_phi_see_a_mask_token(role: Role) -> None:
    result = mask_phi_value(role, "Jane Doe")
    assert result == "[MASKED]"
    assert result != "Jane Doe"


def test_a_none_value_stays_none_regardless_of_role() -> None:
    """No PHI captured at all -- there's nothing to mask, and masking it
    into a token would misrepresent an absent value as a hidden one."""
    assert mask_phi_value(Role.ANALYST, None) is None
    assert mask_phi_value(Role.PLATFORM_ADMIN, None) is None


def test_mask_patient_fields_masks_both_fields_together_for_analyst() -> None:
    name, member_id = mask_patient_fields(
        Role.ANALYST, patient_name="Jane Doe", patient_member_id="M123456"
    )
    assert name == "[MASKED]"
    assert member_id == "[MASKED]"


def test_mask_patient_fields_passes_both_fields_through_for_biller() -> None:
    name, member_id = mask_patient_fields(
        Role.BILLER, patient_name="Jane Doe", patient_member_id="M123456"
    )
    assert name == "Jane Doe"
    assert member_id == "M123456"
