"""FastAPI app factory. Takes its `Repository` and JWT secret as explicit
arguments rather than constructing them internally, so tests can inject a
`FakeRepository` and production wiring (`src/main.py`, Phase 9) can
inject a real `PostgresRepository` without this module knowing the
difference -- same dependency-injection principle as every other
port/adapter in this codebase.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.errors import register_exception_handlers
from api.repository import Repository
from api.request_context import RequestIDMiddleware
from api.routes import audit, contracts, findings, health, packets, remittances


def create_app(*, repository: Repository, jwt_secret_key: str) -> FastAPI:
    app = FastAPI(title="ASC Underpayment Recovery API", version="0.1.0")
    app.state.repository = repository
    app.state.jwt_secret_key = jwt_secret_key

    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(remittances.router)
    app.include_router(findings.router)
    app.include_router(contracts.router)
    app.include_router(audit.router)
    app.include_router(packets.router)

    return app
