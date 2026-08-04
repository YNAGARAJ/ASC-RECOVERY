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
| Encryption at rest for PHI columns (AES-256) | §164.312(a)(2)(iv) | `src/security/encryption.py` — `EnvelopeEncryptor`, AES-256-GCM | Implemented, tested locally |
| Key management, envelope encryption | §164.312(a)(2)(iv), §164.308(a)(5)(ii)(D) | `src/security/kms.py` (port) + `src/security/kms_local.py` (dev/test adapter) | Port + local adapter tested. **Real AWS KMS / Azure Key Vault / GCP KMS / Vault adapters deferred to Phase 9** — no cloud credentials available in this build environment |
| Key rotation without re-encrypting data | §164.308(a)(5)(ii)(D) | `EnvelopeEncryptor.rotate_kek()` re-wraps only the DEK | Implemented, tested — proves ciphertext bytes are unchanged after rotation |
| Transmission encryption (TLS 1.2+) | §164.312(e)(1), §164.312(e)(2)(ii) | Enforced at the ingress/load-balancer layer | **Not yet built** — no network-facing service exists until Phase 6/9 |
| Multi-factor authentication, mandatory, no bypass | §164.312(d) (2026 rule: MFA required) | `src/security/mfa.py` (TOTP) + `src/security/session.py` (`issue_session` refuses without `mfa_verified=True`) | Implemented, tested — see `tests/security/test_session.py::test_only_issue_session_can_mint_a_token_from_a_bare_role` for the no-bypass proof |
| Unique user identification | §164.312(a)(2)(i) | `sub` claim in every session token, per-user | Implemented as part of `session.py` |
| Automatic logoff | §164.312(a)(2)(iii) | Short-lived access tokens (`ACCESS_TOKEN_TTL`, 15 min) | Implemented |
| Forced re-authentication for sensitive actions (PHI export) | §164.312(d) | `session.require_recent_auth()` — checked by callers before allowing export-class actions | Implemented, tested |
| Session/refresh token rotation | §164.312(a)(1) | `session.refresh_session()` — replay of a used refresh token is rejected | Implemented, tested |
| Role-based access control, minimum necessary | §164.312(a)(1), §164.514 (minimum necessary), §164.530(c) | `src/security/rbac.py` — deny-by-default `can(role, action)` | Implemented, full matrix tested |
| Audit controls | §164.312(b) | `audit_log` table (Phase 3), append-only via `REVOKE UPDATE, DELETE` from the app role | Schema + revoke implemented (Phase 3); DB-level enforcement unverified pending live Postgres — see `docs/DB_SETUP.md` |
| PHI access logging, minimum-necessary reporting | §164.514(d), §164.312(b) | `phi_access_log` table (Phase 3) | Same status as audit_log above |
| No PHI in logs, traces, or error messages | §164.312(b), general minimum-necessary principle | `src/security/redaction.py` — `PHIRedactionFilter` | Implemented, tested. Structured `extra=` fields are reliably redacted; free-text regex scrubbing (SSN/MBI shapes) is defense in depth, not a substitute — see the module docstring for the honest limitation |
| Rate limiting | §164.308(a)(5)(ii)(C) (protection from malicious software / brute force, by extension) | `src/security/rate_limit.py` — `InMemoryTokenBucketRateLimiter` | Implemented. **Single-process only** — a Redis-backed adapter is needed once the app runs as more than one instance (same port, new adapter) |
| Account lockout after failed logins | §164.308(a)(5)(ii)(C) | `src/security/rate_limit.py` — `AccountLockoutTracker` | Implemented, tested |
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
  Phase 9.
- **TLS termination / network segmentation** — Phase 9.
- **Application-level encryption actually wired onto the `claims` table's
  PHI columns** (`patient_name`, `patient_member_id` in
  `src/db/models.py`) — the encryption primitive exists now; wiring it
  into the persistence layer's read/write path is follow-on work once
  Phase 5/6 define how claims get written.
- **BAA with the LLM provider, and confirmation of zero-retention terms**
  for the packet-drafting integration added in Phase 7
  (`src/packets/drafter.py`'s `AnthropicPacketDrafter`) — explicitly Phase
  11 scope, per `docs/MASTER-BUILD-PROMPT.md`'s compliance checklist. No
  real LLM call happens anywhere in this codebase's tests; the real
  adapter is untested by design (same deferral as real cloud KMS
  adapters), so there's nothing to retroactively unwind if the BAA terms
  require a different provider or configuration later.

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
