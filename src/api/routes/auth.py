"""`POST /auth/login` -- the sole HTTP entry point that calls
`security.session.issue_session` (F-04, docs/audit/REGISTER.md).
Before this route existed, nothing in the deployed system could actually
authenticate a user: `issue_session`'s MFA-cannot-be-bypassed guarantee
(security/session.py) was real but had zero production callers.

Verifies password then TOTP code, in that order, before minting a
session. Every failure -- unknown subject, no password provisioned,
wrong password, no MFA enrolled, wrong TOTP code -- returns the same
generic 401, so a failed login never discloses which check failed. A
fixed dummy password hash is compared against even when the subject is
unknown, so an unknown-subject response takes the same time as a
known-subject-wrong-password response (a bare "user not found" shortcut
would otherwise make subject existence distinguishable by response
time -- the TOTP check itself is cheap enough relative to the scrypt
hash that it doesn't need the same treatment).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.auth import get_repository
from api.repository import LoginCredentials, Repository
from api.schemas import LoginIn, LoginOut
from security.mfa import verify_code
from security.passwords import hash_password, verify_password
from security.rbac import Role
from security.session import issue_session

router = APIRouter()

_DUMMY_PASSWORD_HASH = hash_password("never-a-real-password-timing-decoy-only")

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
)


def _secret_key(request: Request) -> str:
    key: str = request.app.state.jwt_secret_key
    return key


@router.post("/auth/login", response_model=LoginOut)
def login(
    body: LoginIn,
    request: Request,
    repository: Repository = Depends(get_repository),
) -> LoginOut:
    credentials: LoginCredentials | None = repository.get_login_credentials(body.subject)
    stored_hash = (
        credentials.password_hash
        if credentials is not None and credentials.password_hash is not None
        else _DUMMY_PASSWORD_HASH
    )
    # Called unconditionally -- crucial for the timing-consistency goal
    # above: short-circuiting past this call for an unknown subject would
    # make that case measurably faster than a known-subject wrong
    # password, defeating the whole point of comparing against a dummy
    # hash in the first place.
    password_matches = verify_password(body.password, stored_hash)
    if credentials is None or credentials.password_hash is None or not password_matches:
        raise _INVALID_CREDENTIALS
    # `credentials` is narrowed non-None from here on.
    if credentials.mfa_secret is None or not verify_code(credentials.mfa_secret, body.totp_code):
        raise _INVALID_CREDENTIALS

    tokens = issue_session(
        _secret_key(request), credentials.subject, Role(credentials.role), mfa_verified=True
    )
    return LoginOut(access_token=tokens.access_token, refresh_token=tokens.refresh_token)
