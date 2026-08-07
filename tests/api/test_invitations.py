"""Phase 5 step 3 (`docs/MASTER-BUILD-PROMPT-V2.md`): invite -> accept ->
confirm-mfa -> `POST /auth/login` just works, unchanged. Role gating on
`POST /invitations` mirrors `test_org_members.py`'s shape; the rest is an
end-to-end walk of the three anonymous, token-based routes.
"""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient

from security.rbac import Action, Role, can
from tests.api.conftest import auth_headers

_NEW_SUBJECT = "new-hire@example.com"
_PASSWORD = "correct horse battery staple"


def _totp_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


@pytest.mark.parametrize("role", list(Role))
def test_create_invitation_matrix(client: TestClient, role: Role) -> None:
    response = client.post(
        "/invitations",
        json={"subject": f"invitee-{role.value}@example.com", "role": "biller"},
        headers=auth_headers(role, "a"),
    )
    if not can(role, Action.MANAGE_USERS):
        assert response.status_code == 403, (role, response.text)
        return
    assert response.status_code == 201, (role, response.text)
    body = response.json()
    assert body["token"]
    assert body["subject"] == f"invitee-{role.value}@example.com"
    assert body["role"] == "biller"


def test_create_invitation_requires_facility_ids_for_specific_facilities_scope(
    client: TestClient,
) -> None:
    response = client.post(
        "/invitations",
        json={"subject": _NEW_SUBJECT, "role": "biller", "scope": "SPECIFIC_FACILITIES"},
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    assert response.status_code == 400


def test_preview_unknown_token_is_404(client: TestClient) -> None:
    response = client.get("/invitations/not-a-real-token")
    assert response.status_code == 404


def test_accept_unknown_token_is_404(client: TestClient) -> None:
    response = client.post("/invitations/not-a-real-token/accept", json={"password": _PASSWORD})
    assert response.status_code == 404


def test_confirm_mfa_before_accept_is_404(client: TestClient) -> None:
    created = client.post(
        "/invitations",
        json={"subject": _NEW_SUBJECT, "role": "biller"},
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    token = created.json()["token"]
    response = client.post(f"/invitations/{token}/confirm-mfa", json={"totp_code": "123456"})
    assert response.status_code == 404


def test_full_invitation_lifecycle_then_login(client: TestClient) -> None:
    created = client.post(
        "/invitations",
        json={"subject": _NEW_SUBJECT, "role": "biller"},
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    assert created.status_code == 201
    token = created.json()["token"]

    preview = client.get(f"/invitations/{token}")
    assert preview.status_code == 200
    preview_body = preview.json()
    assert preview_body["subject"] == _NEW_SUBJECT
    assert preview_body["role"] == "biller"
    assert preview_body["status"] == "pending"

    accepted = client.post(f"/invitations/{token}/accept", json={"password": _PASSWORD})
    assert accepted.status_code == 200
    accepted_body = accepted.json()
    assert accepted_body["mfa_secret"]
    assert accepted_body["mfa_provisioning_uri"].startswith("otpauth://")

    # The invitation is no longer usable a second time.
    second_accept = client.post(f"/invitations/{token}/accept", json={"password": _PASSWORD})
    assert second_accept.status_code == 404

    wrong_code = client.post(f"/invitations/{token}/confirm-mfa", json={"totp_code": "000000"})
    assert wrong_code.status_code == 200
    assert wrong_code.json()["verified"] is False

    right_code = client.post(
        f"/invitations/{token}/confirm-mfa",
        json={"totp_code": _totp_code(accepted_body["mfa_secret"])},
    )
    assert right_code.status_code == 200
    assert right_code.json()["verified"] is True

    login = client.post(
        "/auth/login",
        json={
            "subject": _NEW_SUBJECT,
            "password": _PASSWORD,
            "totp_code": _totp_code(accepted_body["mfa_secret"]),
        },
    )
    assert login.status_code == 200
    assert login.json()["access_token"]
