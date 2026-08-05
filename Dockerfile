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
# layer cache isn't invalidated by every source-code change. Dependencies
# install from the pinned lockfile (requirements.lock.txt, generated via
# `make lock` -- see that target's comment in the Makefile), not directly
# from pyproject.toml's version ranges, so a build today and a build in a
# year install byte-for-byte the same versions.
COPY requirements.lock.txt ./
COPY pyproject.toml ./
COPY src/ ./src/

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
# setuptools and msgpack aren't in requirements.lock.txt or anything it
# resolves -- setuptools ships bundled by `python -m venv`'s own bootstrap
# (a known source of stale-setuptools CVE findings in slim Python images);
# msgpack's exact origin wasn't pinned down (not a dependency of anything
# declared here). A plain `--upgrade` wasn't enough: Trivy kept reporting
# the old vulnerable versions (msgpack 1.1.2, setuptools 70.3.0) even
# after the upgrade installed 1.2.1/83.0.0 cleanly alongside them --
# venv-bootstrapped packages can leave stale dist-info metadata that
# `--upgrade` layers on top of instead of replacing. `--force-reinstall`
# removes the old install record instead of upgrading over it.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --force-reinstall --no-deps "setuptools>=78.1.1" "msgpack>=1.2.1" \
 && pip install --no-cache-dir -r requirements.lock.txt \
 && pip install --no-cache-dir --no-deps .

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
