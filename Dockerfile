# syntax=docker/dockerfile:1
#
# Multi-stage build. The `builder` stage has a compiler toolchain and pip
# cache; the `final` stage ships only a venv and this project's source --
# no build tools, no pip, no dev/test dependencies (pytest/ruff/mypy live
# in pyproject.toml's `dev` extra, deliberately not installed here; see
# that file's comment on why the split exists).
#
# Base image tag is pinned by name, not by digest. Pinning to a digest
# requires `docker pull python:3.12-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim`
# against a real registry -- unavailable in the environment this
# Dockerfile was authored in. A fabricated-looking `sha256:...` here
# would be actively misleading; run that command once Docker is
# available (the Codespace this repo is meant to be tested in) and
# replace both FROM lines with the real digest before this ships anywhere
# that matters.
FROM python:3.12-slim AS builder

WORKDIR /build

# Only copy what's needed to resolve dependencies first, so Docker's
# layer cache isn't invalidated by every source-code change.
COPY pyproject.toml ./
COPY src/ ./src/

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

FROM python:3.12-slim AS final

# Non-root user -- the container never needs root once dependencies are
# installed. UID/GID chosen explicitly (not "adduser --system", whose
# allocated id varies by base image) so Terraform/Kubernetes security
# contexts can reference a known, stable value.
RUN groupadd --gid 10001 appuser \
 && useradd --uid 10001 --gid appuser --no-create-home --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED="1"

USER appuser
EXPOSE 8000

# ASGI factory mode, not a bare `main:app` module-level object -- app
# construction (reading env vars, connecting the DB engine lazily,
# building the OTel providers) happens at server startup, not at import
# time. See src/main.py's docstring for why that split matters for
# testability.
ENTRYPOINT ["uvicorn", "main:create_app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
