"""Tests for POST /auth/login (F-04, docs/audit/REGISTER.md) -- the first
route that can actually call `issue_session` in a running deployment.
Every failure mode must return the same generic 401 so a client can't
distinguish "unknown subject" from "wrong password" from "not enrolled in
MFA" from "wrong TOTP code".
"""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient

from security.mfa import generate_enrollment_secret
from security.rbac import Role
from security.session import validate_access_token
from tests.api.conftest import JWT_SECRET
from tests.api.fakes import FakeRepository

_SUBJECT = "biller@example.com"
_PASSWORD = "correct horse battery staple"


@pytest.fixture
def mfa_secret() -> str:
    return generate_enrollment_secret()


def _totp_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def _seed_full_credentials(repo: FakeRepository, mfa_secret: str) -> None:
    repo.seed_login_credentials(
        _SUBJECT, role=Role.BILLER.value, password=_PASSWORD, mfa_secret=mfa_secret
    )


def test_login_succeeds_with_correct_password_and_totp_code(
    client: TestClient, repo: FakeRepository, mfa_secret: str
) -> None:
    _seed_full_credentials(repo, mfa_secret)

    response = client.post(
        "/auth/login",
        json={"subject": _SUBJECT, "password": _PASSWORD, "totp_code": _totp_code(mfa_secret)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    claims = validate_access_token(JWT_SECRET, body["access_token"])
    assert claims.user_id == _SUBJECT
    assert claims.role == Role.BILLER


def test_login_rejects_wrong_password(
    client: TestClient, repo: FakeRepository, mfa_secret: str
) -> None:
    _seed_full_credentials(repo, mfa_secret)

    response = client.post(
        "/auth/login",
        json={"subject": _SUBJECT, "password": "wrong", "totp_code": _totp_code(mfa_secret)},
    )

    assert response.status_code == 401
    assert response.json()["message"] == "invalid credentials"


def test_login_rejects_wrong_totp_code(
    client: TestClient, repo: FakeRepository, mfa_secret: str
) -> None:
    _seed_full_credentials(repo, mfa_secret)

    response = client.post(
        "/auth/login", json={"subject": _SUBJECT, "password": _PASSWORD, "totp_code": "000000"}
    )

    assert response.status_code == 401
    assert response.json()["message"] == "invalid credentials"


def test_login_rejects_unknown_subject(client: TestClient) -> None:
    response = client.post(
        "/auth/login",
        json={"subject": "nobody@example.com", "password": "anything", "totp_code": "123456"},
    )

    assert response.status_code == 401
    assert response.json()["message"] == "invalid credentials"


def test_login_rejects_a_user_with_no_password_provisioned(
    client: TestClient, repo: FakeRepository, mfa_secret: str
) -> None:
    repo.seed_login_credentials(
        _SUBJECT, role=Role.BILLER.value, password=None, mfa_secret=mfa_secret
    )

    response = client.post(
        "/auth/login",
        json={"subject": _SUBJECT, "password": "anything", "totp_code": _totp_code(mfa_secret)},
    )

    assert response.status_code == 401


def test_login_rejects_a_user_not_enrolled_in_mfa(
    client: TestClient, repo: FakeRepository
) -> None:
    repo.seed_login_credentials(
        _SUBJECT, role=Role.BILLER.value, password=_PASSWORD, mfa_secret=None
    )

    response = client.post(
        "/auth/login",
        json={"subject": _SUBJECT, "password": _PASSWORD, "totp_code": "123456"},
    )

    assert response.status_code == 401


def test_login_never_calls_issue_session_when_password_is_wrong(
    client: TestClient, repo: FakeRepository, mfa_secret: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correct TOTP code alone must never be enough -- both checks are
    required, and a wrong password must short-circuit before minting."""
    _seed_full_credentials(repo, mfa_secret)
    import api.routes.auth as auth_module

    def _fail_if_called(*_args: object, **_kwargs: object) -> None:
        pytest.fail("issue_session must not be called")

    monkeypatch.setattr(auth_module, "issue_session", _fail_if_called)

    response = client.post(
        "/auth/login",
        json={"subject": _SUBJECT, "password": "wrong", "totp_code": _totp_code(mfa_secret)},
    )

    assert response.status_code == 401


# --- F-06: account lockout (security.rate_limit.AccountLockoutTracker) ------


def test_login_locks_out_after_repeated_failures(
    client: TestClient, repo: FakeRepository, mfa_secret: str
) -> None:
    _seed_full_credentials(repo, mfa_secret)
    wrong = {"subject": _SUBJECT, "password": "wrong", "totp_code": _totp_code(mfa_secret)}
    for _ in range(5):  # AccountLockoutTracker's default max_failures
        assert client.post("/auth/login", json=wrong).status_code == 401

    # The account is now locked out -- even the *correct* password+TOTP
    # code is rejected, not just another wrong one.
    correct = {"subject": _SUBJECT, "password": _PASSWORD, "totp_code": _totp_code(mfa_secret)}
    response = client.post("/auth/login", json=correct)
    assert response.status_code == 401


def test_locked_out_account_never_reaches_issue_session(
    client: TestClient, repo: FakeRepository, mfa_secret: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_full_credentials(repo, mfa_secret)
    wrong = {"subject": _SUBJECT, "password": "wrong", "totp_code": _totp_code(mfa_secret)}
    for _ in range(5):
        client.post("/auth/login", json=wrong)

    import api.routes.auth as auth_module

    def _fail_if_called(*_args: object, **_kwargs: object) -> None:
        pytest.fail("issue_session must not be called for a locked-out account")

    monkeypatch.setattr(auth_module, "issue_session", _fail_if_called)

    correct = {"subject": _SUBJECT, "password": _PASSWORD, "totp_code": _totp_code(mfa_secret)}
    response = client.post("/auth/login", json=correct)
    assert response.status_code == 401


def test_login_success_resets_the_failure_count(
    client: TestClient, repo: FakeRepository, mfa_secret: str
) -> None:
    _seed_full_credentials(repo, mfa_secret)
    wrong = {"subject": _SUBJECT, "password": "wrong", "totp_code": _totp_code(mfa_secret)}
    correct = {"subject": _SUBJECT, "password": _PASSWORD, "totp_code": _totp_code(mfa_secret)}

    for _ in range(4):  # below the default 5-failure threshold
        assert client.post("/auth/login", json=wrong).status_code == 401
    assert client.post("/auth/login", json=correct).status_code == 200

    # A successful login resets the count -- four more failures alone
    # must not trip the lockout a second time.
    for _ in range(4):
        assert client.post("/auth/login", json=wrong).status_code == 401
    assert client.post("/auth/login", json=correct).status_code == 200


def test_lockout_is_scoped_to_the_subject(
    client: TestClient, repo: FakeRepository, mfa_secret: str
) -> None:
    other_subject = "other-biller@example.com"
    other_mfa_secret = generate_enrollment_secret()
    _seed_full_credentials(repo, mfa_secret)
    repo.seed_login_credentials(
        other_subject, role=Role.BILLER.value, password=_PASSWORD, mfa_secret=other_mfa_secret
    )

    wrong = {"subject": _SUBJECT, "password": "wrong", "totp_code": _totp_code(mfa_secret)}
    for _ in range(5):
        client.post("/auth/login", json=wrong)

    response = client.post(
        "/auth/login",
        json={
            "subject": other_subject,
            "password": _PASSWORD,
            "totp_code": _totp_code(other_mfa_secret),
        },
    )
    assert response.status_code == 200


def test_login_validation_error_never_echoes_the_submitted_password(client: TestClient) -> None:
    secret_password = "super-secret-marker-value-should-not-leak"
    response = client.post(
        "/auth/login",
        json={"subject": _SUBJECT, "password": secret_password, "totp_code": 12345},
    )

    assert response.status_code == 422
    assert secret_password not in response.text
    body = response.json()
    assert set(body) == {"error", "message", "request_id", "errors"}
    for error in body["errors"]:
        assert set(error) == {"loc", "type", "msg"}
