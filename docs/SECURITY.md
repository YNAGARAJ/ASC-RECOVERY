# Security controls (Phase 4, extended in Phase 7)

This maps each security control to its HIPAA Security Rule citation, where
it lives in the codebase, and its verification status. Built in Phase 4;
extended in Phase 7 with the LLM-prompt/output controls below, since that's
the first phase where PHI or a dollar figure could reach a third-party
service. This is engineering documentation, not legal advice — it must be
reviewed by counsel/compliance as part of Phase 11 before any real PHI
touches this system.

The 2026 HIPAA Security Rule update made encryption and multi-factor
authentication **required**, not "addressable" as under the prior rule.
Every control below is written to that bar.

## Control matrix

| Control | HIPAA citation | Implementation | Status |
|---|---|---|---|
| Encryption at rest for PHI columns (AES-256) | §164.312(a)(2)(iv) | `src/security/encryption.py` — `EnvelopeEncryptor`, AES-256-GCM; wired onto `claims.patient_name_encrypted`/`patient_member_id_encrypted` in `ingestion/apply.py` (write) and `api/repository.py` (read) as of Phase 10 | Implemented and wired end to end, tested (`tests/db/test_patient_columns_are_encrypted.py`, `tests/api/test_endpoints_live_db.py`). Previously the primitive existed but nothing called it — patient columns were plaintext in practice; closed as a Phase 10 adversarial-review HIGH finding |
| Key management, envelope encryption | §164.312(a)(2)(iv), §164.308(a)(5)(ii)(D) | `src/security/kms.py` (port) + `src/security/kms_local.py` (dev/test adapter) + `src/security/kms_env.py` (`EnvKMS`, a static-KEK-from-secret stopgap wired into `main.py` for real deployments) | Port, local adapter, and `EnvKMS` stopgap all implemented and tested. **Real AWS KMS / Azure Key Vault / GCP KMS / Vault adapters remain a named, deferred gap** — no cloud credentials available in this build environment. `EnvKMS` is meaningfully weaker (no per-operation KMS-side audit trail, no automatic rotation) but is a real, working encryption-at-rest mechanism today, not a placeholder |
| Key rotation without re-encrypting data | §164.308(a)(5)(ii)(D) | `EnvelopeEncryptor.rotate_kek()` re-wraps only the DEK | Implemented, tested — proves ciphertext bytes are unchanged after rotation |
| Transmission encryption (TLS 1.2+) | §164.312(e)(1), §164.312(e)(2)(ii) | Enforced at the ingress/load-balancer layer | **Not yet built** — no network-facing service exists until Phase 6/9 |
| Multi-factor authentication, mandatory, no bypass | §164.312(d) (2026 rule: MFA required) | `src/security/mfa.py` (TOTP) + `src/security/session.py` (`issue_session` refuses without `mfa_verified=True`) | Implemented, tested — see `tests/security/test_session.py::test_only_issue_session_can_mint_a_token_from_a_bare_role` for the no-bypass proof |
| Unique user identification | §164.312(a)(2)(i) | `sub` claim in every session token, per-user | Implemented as part of `session.py` |
| Automatic logoff | §164.312(a)(2)(iii) | Short-lived access tokens (`ACCESS_TOKEN_TTL`, 15 min) | Implemented |
| Forced re-authentication for sensitive actions (PHI export) | §164.312(d) | `session.require_recent_auth()` — checked by callers before allowing export-class actions | Implemented, tested |
| Session/refresh token rotation | §164.312(a)(1) | `session.refresh_session()` — replay of a used refresh token is rejected | Implemented, tested |
| Role-based access control, minimum necessary | §164.312(a)(1), §164.514 (minimum necessary), §164.530(c) | `src/security/rbac.py` — deny-by-default `can(role, action)` | Implemented, full matrix tested |
| Audit controls | §164.312(b) | `audit_log` table (Phase 3), append-only via `REVOKE UPDATE, DELETE` from the app role; ingestion writes `claim`/`finding`-resource entries (`ingestion/apply.py`), not just a batch-level `remittance_ingested` row, as of Phase 10 | Schema + revoke implemented (Phase 3); claim/finding-level coverage closed as a Phase 10 adversarial-review HIGH finding (`GET /claims/{id}/access-history` previously couldn't show a claim's own ingestion). DB-level append-only enforcement now runs for real on every push via `.github/workflows/ci.yml`'s Postgres service container, not just pending-verification |
| PHI access logging, minimum-necessary reporting | §164.514(d), §164.312(b) | `phi_access_log` table (Phase 3) | Same status as audit_log above |
| No PHI in logs, traces, or error messages | §164.312(b), general minimum-necessary principle | `src/security/redaction.py` — `PHIRedactionFilter` | Implemented, tested. Structured `extra=` fields are reliably redacted; free-text regex scrubbing (SSN/MBI shapes) is defense in depth, not a substitute — see the module docstring for the honest limitation |
| Rate limiting | §164.308(a)(5)(ii)(C) (protection from malicious software / brute force, by extension) | `src/security/rate_limit.py` — `InMemoryTokenBucketRateLimiter`; `src/api/rate_limit.py::enforce_rate_limit` | Module implemented and unit-tested in isolation, but **not wired into any API route** as of Phase 10 (a Phase 10 adversarial-review MEDIUM finding) — every route depends only on `require_permission`, so PHI-reading endpoints have no request throttling today. Also single-process only; a Redis-backed adapter is needed once the app runs as more than one instance |
| Account lockout / step-up re-auth after failed logins or before PHI export | §164.308(a)(5)(ii)(C), §164.312(d) | `src/security/rate_limit.py::AccountLockoutTracker`; `src/security/session.py::require_recent_auth` | Modules implemented and unit-tested in isolation, but **neither has any call site** as of Phase 10 (a Phase 10 adversarial-review MEDIUM finding) — login has no lockout, and no route requires step-up re-authentication before returning patient PHI |
| Secret management, nothing committed | §164.308(a)(3) (workforce security, by extension), general security hygiene | `src/security/secrets.py` — `SecretStore` port, `EnvSecretStore` dev adapter | Port + dev adapter implemented. Real Vault/cloud secret-store adapters are Phase 9/11 scope |
| Static application security testing | §164.308(a)(8) (evaluation) | `bandit` (`make security`) | Runs clean (`bandit -r . -x ./tests,./evals` — the Makefile's original `-x tests,evals` exclude syntax didn't actually exclude anything on this platform; fixed to `-x ./tests,./evals`) |
| Dependency vulnerability scanning | §164.308(a)(8) | `pip-audit` (`make security`) | **This project's actual dependencies are clean.** `pip-audit` reports 14 findings, but every one is in `dulwich`/`msgpack` (transitive deps of `poetry`/`CacheControl` — unrelated tools sharing this machine's global Python install) or in `pip` itself — none in `sqlalchemy`, `alembic`, `psycopg`, `cryptography`, `pyjwt`, or `pyotp`. This surfaces a real gap: **the project has no isolated virtual environment**, so `pip-audit` (and anyone reproducing this) audits the whole shared install rather than just this project's dependency tree. Worth fixing (a project-local venv or lockfile) before this scan result can be trusted at face value in CI |
| Full-history secret scanning | §164.308(a)(1)(ii)(D) (information system activity review, by extension) | `gitleaks` (`make security`) | **Not run** — `gitleaks` is a Go binary, not installable via `pip`, and unavailable in this build environment. Must be run before Phase 11 |
| Minimum-necessary PHI in LLM prompts | §164.514 (minimum necessary) | `src/packets/prompt.py` — patient name/member id are never interpolated into the text sent to the LLM; they're substituted back into the final letter only after generation | Implemented, tested — `tests/packets/test_prompt.py` asserts directly on the captured prompt text for a range of inputs, including distinctive names chosen so a false negative can't happen by luck |
| Integrity control on LLM-generated content | §164.312(c)(1) (integrity) | `src/packets/currency.py` + `src/packets/service.py` — every dollar figure in a generated appeal letter is extracted and asserted to exactly match a value in the deterministic finding record; the draft is rejected and regenerated (up to a small retry cap) if not, and no unvalidated draft is ever persisted or returned | Implemented, tested — `tests/packets/test_currency.py`, `test_service.py`. This is CLAUDE.md's hardest rule: an LLM never computes, adjusts, or restates a dollar amount |

## Not yet built (later phases, by design)

- **OIDC identity provider integration.** `session.py` builds what happens
  *after* a successful login (MFA-gated token issuance, refresh, and
  validation); the actual OIDC handshake needs a real IdP and a hosting
  API, both of which arrive in Phase 6.
- **Real cloud KMS adapters** (AWS KMS, Azure Key Vault, GCP KMS, Vault) —
  named, deferred; `security/kms_env.py`'s `EnvKMS` is the real-but-weaker
  stopgap in the meantime (see the key management row above).
- **TLS termination / network segmentation** — Phase 9 (Terraform layer);
  Phase 10 added explicit RDS-to-app encryption in transit
  (`terraform/modules/aws/database.tf`'s `rds.force_ssl` parameter group —
  RDS defaults to allowing unencrypted connections, Azure's flexible
  server does not).
- **BAA with the LLM provider, and confirmation of zero-retention terms**
  for the packet-drafting integration added in Phase 7
  (`src/packets/drafter.py`'s `AnthropicPacketDrafter`) — explicitly Phase
  11 scope, per `docs/MASTER-BUILD-PROMPT.md`'s compliance checklist. No
  real LLM call happens anywhere in this codebase's tests; the real
  adapter is untested by design (same deferral as real cloud KMS
  adapters), so there's nothing to retroactively unwind if the BAA terms
  require a different provider or configuration later.

## Phase 10 adversarial review: triaged findings not fixed this phase

`docs/MASTER-BUILD-PROMPT.md` requires a full adversarial-review pass
before Phase 10 closes, with every HIGH/CRITICAL finding fixed before
proceeding. Both HIGH findings (plaintext PHI columns; missing claim/
finding audit entries, both above) were fixed. The MEDIUM/LOW findings
below were explicitly triaged rather than silently dropped — each is a
real, scoped gap, deliberately not rushed into a fix this phase:

- **LLM currency validation is membership-only, not positional.**
  `packets/currency.py::validate_currency` confirms every dollar figure in
  a generated letter matches *some* allowed value, but not that it's
  labeled correctly (a draft could put the shortfall figure where the
  expected-allowed amount belongs). Mitigated today by human review —
  every packet requires biller approval (`POST /packets/{id}/approve`)
  before it can be used — but that's a backstop, not a fix. A real fix
  means either a much more rigid, single-blank-per-sentence template (no
  LLM freedom over figure placement) or a second verification pass;
  both are real design decisions, not something to guess at under time
  pressure.
- **Rate limiting, account lockout, and step-up re-auth are unwired**
  (see the control matrix rows above) — the modules exist and are
  tested in isolation, but no route calls them. Wiring them requires
  policy decisions (which routes need step-up? what limits, per what
  key?) that deserve a real conversation, not an assumed default.
- **`list_findings_by_payer_claim_control_number` was missing its
  tenant_id filter** despite accepting the parameter — fixed in Phase 10
  (`src/db/repository.py`); safe today only because every caller runs
  inside `tenant_session` (RLS-scoped), but it was a latent global-read
  footgun for any future caller that didn't.
- **Reversal netting is incomplete.** A reversal (CLP02=22) creates an
  offsetting finding but doesn't mark/supersede the original, so both
  rows persist; if the reversal's line layout doesn't match the original,
  the offsetting finding is silently dropped. Affects the worklist's
  accuracy, not stored dollar amounts. Deferred — the correct fix (an
  explicit supersession model) touches `domain.variance`, `ingestion.plan`,
  and `ingestion.apply` together and deserves its own design pass.
- **PLB sign convention in `ingestion/reconcile.py` may not match the
  X12 835 TR3 guide.** Flagged by the adversarial review; the existing
  test suite (`tests/ingestion/test_reconcile.py`) locks in the current
  convention with a fixture built consistently around it, and X12's PLB
  sign semantics are a well-known source of implementer disagreement.
  Changing this without checking the TR3 guide risks trading one
  plausible-but-wrong convention for another. Affects only the
  `reconciliation_mismatches` diagnostic counter, never a computed
  finding or dollar amount — deferred pending a real spec check.
- **Free-text PHI redaction regexes only match dashed SSN/MBI shapes**
  (`security/redaction.py`) — undashed 9-digit sequences pass through.
  This is explicitly documented as defense-in-depth, not the primary
  control (structured `extra=` field redaction is), but the gap is real.
- **`GET /findings/{id}` returns full patient name/member id to any role
  with `READ_FINDING`, including `VIEWER`.** Within-tenant, audited via
  `phi_access_log`, so not a leak — a minimum-necessary policy question
  (should a read-only role see raw identifiers?) worth raising with
  whoever owns that product decision, not something to unilaterally
  narrow.

## Asset inventory and network map

The 2026 rule requires both, reviewed annually. This is the template Phase
4 establishes; it gets filled in as real infrastructure exists (Phase 9
onward) and reviewed for the first time before Phase 11.

**Asset inventory (template):**

| Asset | Type | Data classification | Owner | Last reviewed |
|---|---|---|---|---|
| Postgres primary DB | Data store | PHI | — | not yet in service |
| Application servers | Compute | Processes PHI | — | not yet in service |
| KMS / key store | Key management | Protects PHI encryption keys | — | not yet in service |
| Object storage (835 files) | Data store | PHI | — | not yet in service |

**Network map:** to be produced in Phase 9 once actual network topology
(VPC/subnets, ingress, segmentation) exists — a diagram of a system that
isn't deployed yet would be fiction, not documentation.

## Incident response and breach notification

Full procedures (60-day HIPAA clock, named owner, tested contact tree) are
Phase 11 scope — that phase is explicitly paperwork/process, not code, and
shouldn't be drafted piecemeal alongside unrelated engineering phases.
