"""GET /healthz (liveness) and GET /readyz (readiness) -- no
authentication, since infra health probes (container orchestrator load
balancer) don't carry a bearer token.

Liveness and readiness answer different questions and must stay separate:
`/healthz` only proves the process is up and serving HTTP -- it never
checks a dependency, so a container orchestrator never restarts a
perfectly healthy process just because the database blipped.
`/readyz` checks `Repository.ping()` and answers "can this instance
serve real traffic right now" -- a load balancer pulls a not-ready
instance out of rotation without killing it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.auth import get_repository
from api.repository import Repository

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(repository: Repository = Depends(get_repository)) -> JSONResponse:
    try:
        ready = repository.ping()
    except Exception:
        # Any dependency failure means "not ready" -- this endpoint's
        # entire job is to turn an unknown failure mode into a 503,
        # never to propagate or crash on one.
        ready = False
    if not ready:
        return JSONResponse(status_code=503, content={"status": "not ready"})
    return JSONResponse(status_code=200, content={"status": "ready"})
