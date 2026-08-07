# ASC Underpayment Recovery Platform

## What this is
A system that reads X12 835 remittance files from health insurers, compares
what was paid against a contracted fee schedule, and finds claims that were
underpaid. Customers are ambulatory surgery centers. The data is protected
health information (PHI), so security is not optional.

## Stack
Python 3.12 · FastAPI · PostgreSQL 16 · SQLAlchemy 2.x · Alembic · Pydantic v2
· pytest · Docker · Terraform · OpenTelemetry

## Non-negotiable rules
1. No real PHI in this repo, tests, logs, fixtures, or prompts. Synthetic
   data only. If you think you need real data, stop and ask.
2. Money is `Decimal`, never `float`. Rounding is ROUND_HALF_UP, 2 places.
3. No LLM ever computes or restates a dollar amount. LLMs draft prose only;
   figures are injected deterministically and validated after generation.
4. Claims are priced against the contract version effective on the claim's
   DATE OF SERVICE, never today's contract.
5. Every write to a PHI-bearing table goes through the audit log. No
   exceptions.
6. No PHI in logs, traces, error messages, or URLs. Assume every log line
   will be read by someone unauthorized.
7. Cloud-agnostic. No proprietary managed service without an equivalent on
   another cloud.
8. Multi-tenant. Every query is scoped by resolved facility/org access
   (`organizations` → `facilities` → `memberships`, walked recursively —
   see `alembic/versions/0001_initial_schema.py`'s
   `resolve_accessible_facility_ids`/`resolve_accessible_org_ids` and
   `docs/PERMISSIONS.md`). There is no global read, and no bare tenant id
   to scope by — access is never a flat equality check.

## Commands
- `make test` — full test suite, must be green before any commit
- `make lint` — ruff + mypy strict
- `make eval` — golden-dataset accuracy check
- `make security` — bandit, pip-audit, secret scan

## Definition of done
Tests written and passing · types clean · no PHI leak · audit entry where
applicable · `docs/` updated if behavior changed.

See `docs/PHASES.md` for the current build phase.
