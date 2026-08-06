# Audit — Wave 1, Agent 5: Security & PHI

Read-only. No application code was modified to produce this file. Every claim
below was verified by reading the actual source this session, not copied from
Wave 0. Where Wave 0's conformance map reached the same conclusion, that is
noted; where this pass found more, that is called out.

Environment caveats (same as `00-baseline.md`): no live Postgres, no Docker, no
`gitleaks`/`pip-audit` re-run this session. Anything that can only be proven
against a running database (RLS actually enforcing) is flagged as such and
cross-referenced to Agent 6.

**Tally:** 0 CRITICAL · 5 HIGH · 6 MEDIUM · 3 LOW.

---

## Authentication, MFA, session

### [HIGH] No authentication path exists — `issue_session` (the sole MFA choke point) is never called
- **File:** src/security/session.py:68 (`issue_session`); src/api/auth.py (whole module); src/api/routes/ (no login/enroll/verify route)
- **What breaks:** The system cannot authenticate a real user. `issue_session()` is the only function that mints a session and the only place `mfa_verified` is checked — grep confirms it has **zero callers in `src/`** (only `tests/`). There is no `/login`, `/token`, `/mfa/enroll`, or `/mfa/verify` route anywhere. In production (`main.py`) the app is wired with a `jwt_secret_key` and `get_auth_context` validates already-minted tokens, but nothing in the running system ever issues one. Whatever process mints production JWTs is undefined and therefore ungoverned by the MFA gate. Wave 0 flagged MFA as unenforced; this is the sharper root cause — the entire credential/issuance layer is absent, so `issue_session`'s MFA check is unreachable.
- **Reproduce:** `grep -rn "issue_session\|refresh_session" src/ | grep -v "def "` → only docstrings in session.py. `ls src/api/routes/` → no auth route.
- **Fix:** Build a login route (OIDC or credential+MFA) that is the single caller of `issue_session`, and make it the only issuer of production tokens. Until then treat token issuance as an unsecured boundary.
- **Effort:** L

### [HIGH] MFA is not just unenforced — it is unstorable
- **File:** src/security/mfa.py:17 (`generate_enrollment_secret` says the secret "must be stored encrypted"); src/db/models.py:58 (`User` model)
- **What breaks:** The `users` table has **no column** for an MFA secret, password hash, or any credential — only `id`, `tenant_id`, `subject`, `role`, timestamps. There is nowhere to persist an enrollment secret, so `mfa.py`'s functions (`generate_enrollment_secret`, `provisioning_uri`, `verify_code`) cannot be wired to a real user even if a route were added. `mfa.py` has zero callers in `src/`. MFA — a named 2026 HIPAA Security Rule requirement in the master prompt — is impossible today, not merely unwired. "MFA cannot be bypassed" is vacuously true because there is nothing to bypass and nowhere to store what would be bypassed.
- **Reproduce:** `grep -rn "verify_code\|generate_enrollment_secret" src/` → only the defs. Read `User` model: no secret/credential column.
- **Fix:** Add an encrypted `mfa_secret` column (via `phi_columns`/`EnvelopeEncryptor`) plus enrollment/verification routes that call `mfa.verify_code` before `issue_session(..., mfa_verified=True)`.
- **Effort:** L

### [HIGH] Rate limiting and account lockout are wired to nothing
- **File:** src/api/rate_limit.py:16 (`enforce_rate_limit`); src/security/rate_limit.py:79 (`AccountLockoutTracker`)
- **What breaks:** `enforce_rate_limit` has **zero references anywhere in the repo** — not a single `Depends(enforce_rate_limit)` on any route, and no test. `AccountLockoutTracker` likewise has no caller in `src/`. Every endpoint (including whatever future login route appears) is unthrottled: unbounded brute-force / credential-stuffing against auth, and unbounded request volume against PHI-read endpoints (`GET /findings`, `GET /findings/{id}` which decrypts PHI on every call). This sharpens Wave 0's orphan flag into an exact exposure.
- **Reproduce:** `grep -rn "enforce_rate_limit\|AccountLockoutTracker" src/` → only the definitions.
- **Fix:** Attach `enforce_rate_limit` as a router-level dependency; wire `AccountLockoutTracker` into the login route once it exists. Note both are in-memory/single-process (documented) and need a shared-store adapter before running >1 instance.
- **Effort:** M

### [MEDIUM] Step-up re-auth (`require_recent_auth`) never guards any PHI export
- **File:** src/security/session.py:127 (`require_recent_auth`); src/api/routes/findings.py:66 (`export_findings_csv`)
- **What breaks:** `require_recent_auth` (Phase 4's "forced re-auth for PHI export") has zero callers in `src/`. The CSV worklist export and the single-finding detail view (both PHI-bearing) enforce only ordinary permission, never freshness. A 15-minute access token (or a session left open) can bulk-export PHI with no re-authentication, contrary to the Phase 4 deliverable.
- **Reproduce:** `grep -rn "require_recent_auth" src/` → only its def + docstring. Read `export_findings_csv` — no freshness check.
- **Fix:** In `export_findings_csv` (and ideally `get_finding_detail`), read the token's `auth_time` and reject with 401 forcing re-login when `require_recent_auth` is False.
- **Effort:** S

### [LOW] JWT secret minimum length is documented but not enforced
- **File:** src/security/session.py:41-42 (comment: "secret_key must be >= 32 bytes"); :135 (`_mint_pair`)
- **What breaks:** Nothing validates the JWT secret length at issuance or app startup. A misconfigured short/low-entropy `JWT_SECRET_KEY` is accepted silently, weakening every token's HMAC.
- **Reproduce:** Set `JWT_SECRET_KEY=x` and construct the app — no error.
- **Fix:** Assert `len(secret_key.encode()) >= 32` in `create_app`/`create_app_from_env`.
- **Effort:** S

---

## Encryption at rest / in transit / key management

### [HIGH] Client↔API TLS is not enforced or codified anywhere
- **File:** terraform/modules/aws/container_runtime.tf:210-231 (HTTP target group, HTTPS listener deferred); terraform/environments/aws/main.tf (never wires the listener/cert either); src/db/base.py:13 (`make_engine`)
- **What breaks:** The ALB target group is `protocol = "HTTP"` on port 8000; the HTTPS listener + ACM certificate are explicitly deferred to "environment root config," but the environment root (`environments/aws/main.tf`) does **not** wire them. So no committed IaC actually terminates TLS for client traffic, there is no HTTP→HTTPS redirect, the app serves plain HTTP with no HSTS header, and `make_engine` does not force `sslmode` if the URL omits it. "TLS enforced" is documented but not realized for the client edge. Anyone deploying from this Terraform as-is exposes PHI-bearing API traffic in cleartext. (DB-layer TLS *is* enforced on AWS — see clean note below.)
- **Reproduce:** Read the AWS module + env root: no `aws_lb_listener` on 443, no `redirect` action, no HSTS middleware in `api/app.py`.
- **Fix:** Add an `aws_lb_listener` (443, ACM cert, modern `ssl_policy`) + a port-80 redirect listener; add HSTS in the app or at the ALB; equivalently for Azure. Have `make_engine` append/require `sslmode=require` defensively.
- **Effort:** M

### [MEDIUM] Field-level PHI encryption covers only two columns; other identifiers sit in plaintext
- **File:** src/security/phi_columns.py:3 (only `patient_name_encrypted`, `patient_member_id_encrypted`); src/db/models.py:179 (`claims`)
- **What breaks:** Envelope encryption is applied only to patient name and member id. `patient_control_number`, `payer_claim_control_number`, `date_of_service`, procedure codes and amounts are stored in plaintext, protected only by disk-level encryption. Several of these (claim/account numbers, service dates) are HIPAA identifiers. Any exposure that bypasses the disk layer — a backup, a read replica, a logical dump, a SQL-injection read — yields re-identifiable claim data. The 2026 rule's push toward application-layer encryption is only half-met.
- **Reproduce:** Read `claims` model: only two `*_encrypted` columns; the rest are plaintext types.
- **Fix:** Decide explicitly which identifiers require app-layer encryption vs. tokenization; extend `phi_columns` to cover claim control numbers and DOS if in scope, or document the risk acceptance.
- **Effort:** M

### [MEDIUM] KEK rotation is impossible with the only production KMS adapter
- **File:** src/security/encryption.py:62 (`rotate_kek`); src/security/kms_env.py:27,46,53 (`EnvKMS`, single static `env-kek-v1`)
- **What breaks:** `rotate_kek(payload, new_kek_id)` correctly re-wraps just the DEK without touching ciphertext — the rotation *logic* is right and cheap. But `EnvKMS` (the only non-test adapter, wired in `main.py`) knows exactly one `kek_id` (`env-kek-v1`) and raises `KeyError` for any other. There is no way to introduce a second KEK, so rotation can never actually run in production; it is exercised only against `LocalKMS` (dev/test) which supports `generate_kek`. Combined with the documented absence of any real cloud-KMS adapter, key rotation — a HIPAA expectation — is unachievable without a code change.
- **Reproduce:** Call `rotate_kek(p, "env-kek-v2")` against an `EnvKMS`-backed encryptor → `KeyError`, then `DecryptionError`.
- **Fix:** Give `EnvKMS` a multi-key map keyed by id (e.g. `PHI_ENCRYPTION_KEY_V1/_V2` with a "current" pointer), or ship a real cloud-KMS adapter that supports versioned keys.
- **Effort:** M

### [LOW] Azure Postgres does not explicitly require secure transport
- **File:** terraform/modules/azure/database.tf:13 (`azurerm_postgresql_flexible_server`)
- **What breaks:** No `require_secure_transport` server parameter is set. Azure defaults it ON, so this is likely fine in practice, but the AWS module makes TLS explicit (`rds.force_ssl=1`) and the Azure module leaves it to a provider default — an easy silent regression if the default changes or someone flips it.
- **Reproduce:** Read the Azure DB resource: no `azurerm_postgresql_flexible_server_configuration` for `require_secure_transport`.
- **Fix:** Add an explicit `require_secure_transport = ON` configuration resource, mirroring the AWS `force_ssl`.
- **Effort:** S

---

## PHI in logs, traces, exceptions, prompts, fixtures, history

### [HIGH] LLM prompt ships claim identifiers to a third party in production; docstring wrongly calls them "not PHI"
- **File:** src/packets/prompt.py:1-12 (docstring), :63-65 (prompt includes `payer_claim_control_number`, `date_of_service`, `procedure_code` as real values); src/main.py:95 (real `AnthropicPacketDrafter` wired in production)
- **What breaks:** `build_prompt` correctly keeps patient name and member id out of the prompt text (placeholder substitution — verified, and genuinely good). But it embeds the **payer claim control number and date of service** as literal values, and the docstring asserts these "are not PHI." Under HIPAA, claim/account numbers and service dates are enumerated identifiers. In production the *real* Anthropic adapter is wired (`main.py:95`) and `POST /findings/{id}/packets` calls it, so generating a packet transmits re-identifiable claim data to a third party. Absent a signed BAA (Phase 11, not closed), that is an impermissible disclosure. The incorrect docstring is itself a hazard: it invites someone to consider the prompt "PHI-clean" when it is not.
- **Reproduce:** Read `build_prompt`: `f"Claim reference: {data.payer_claim_control_number}\nProcedure code: ...\nDate of service: ..."`. Trace `generate_packet` (api/repository.py:671) → `PromptInput` carries these → `drafter.draft(prompt.text)`.
- **Fix:** Replace the claim reference and DOS in the prompt with placeholder tokens (same mechanism already used for name/amounts), substituting real values only in `render_final_text`. Correct the docstring. Do not enable the real drafter until a BAA is in place.
- **Effort:** M

### [MEDIUM] PHI redaction filter is attached to exactly one logger; there is no global logging config
- **File:** src/api/errors.py:19-20 (`logger.addFilter` on `"api.errors"` only); src/security/redaction.py:60; src/main.py (no logging config)
- **What breaks:** `PHIRedactionFilter` is installed on the single `api.errors` logger. Nothing configures the root logger or any other logger (`api.request`, ingestion, packets, db). Python logger-level filters do **not** apply to records emitted by other loggers, so any log line produced anywhere else in the app is entirely unredacted. Today no other module logs PHI, so this is a latent gap rather than an active leak — but "redact at the source" is only realized for one code path, and the next `logger.info(f"...{patient_name}...")` added anywhere would bypass it silently. Additionally, `scrub_text` only catches SSN/MBI-shaped substrings (redaction.py:82); a patient *name* interpolated into a message string is never caught by design.
- **Reproduce:** `grep -rn "addFilter\|basicConfig\|dictConfig" src/` → one hit, in errors.py.
- **Fix:** Install `PHIRedactionFilter` on the root logger's handlers (filters on *handlers* apply to all propagated records) via a central `logging.dictConfig` in `main.py`/`create_app`. Keep structured-`extra=` discipline as the primary mechanism.
- **Effort:** M

### [CLEAN] Exception handling does not echo PHI to clients
- **File:** src/api/errors.py:32-55
- **Evidence:** The generic 500 handler returns a fixed `"an unexpected error occurred"` and logs the traceback only through the redaction-filtered `api.errors` logger (exc_text is pre-scrubbed in redaction.py:72-77). The `HTTPException` handler returns `str(exc.detail)`, but every `detail` in the routes is a developer-authored constant ("finding not found", "missing bearer token", etc.) — none interpolate request data or PHI. Auth-failure paths return fixed strings, never the underlying JWT error text. No leak found.

### [CLEAN] Tracing scrubs at source with a defensible design
- **File:** src/observability/tracing.py
- **Evidence:** No auto-instrumentation (which could capture raw SQL/route params); all spans are created manually with explicit attributes; `PHIScrubbingSpanExporter` wraps any real exporter and redacts `PHI_FIELD_NAMES` keys + SSN/MBI patterns on string values via a fresh `ReadableSpan`. Same regex limitation as logging (won't catch a free-text name), but the manual-span discipline keeps names out of attributes in the first place. Reasonable defense-in-depth.

### [CLEAN] Test fixtures are synthetic
- **File:** tests/domain/fixtures_x835.py:1-6, :144-153; tests/domain/conftest.py:1-35
- **Evidence:** Names are `PATIENT ONE` / `TESTFIRST`, member ids `TESTMBR000001`, providers `TEST RENDERING PROVIDER`, NPIs `1999999999` — deliberately shaped to avoid SSN/MBI patterns per the block-phi hook. Grep for SSN-shaped strings and realistic names across `tests/` and `evals/` found nothing that reads as real PHI.

### [CLEAN] No secrets in tracked files or git history
- **Evidence:** `git grep` for private-key headers, AWS keys, `sk-ant-`, Slack tokens across **all** commits returned only the `scripts/hooks/block_phi.sh` regex *pattern* string (the detector's own source), never a credential. Baseline synthetic test secrets are documented allowlist entries. `EnvSecretStore`/`EnvKMS` read from the process environment at runtime; nothing is committed. (Note: `gitleaks` itself was not runnable this session — last real full-history scan was Phase 10 CI, predating Phase 11/12 files.)

---

## Input validation

### [MEDIUM] Unvalidated money/rate strings produce 500s, not 422s; recovery amount unconstrained
- **File:** src/api/schemas.py:184-216 (rate/fee strings), :139-144 (`RecordOutcomeIn.amount_recovered`); src/api/repository.py:253 (`Money(amount)`), :255-273 (`Rate.percent(...)`); src/api/routes/findings.py:132 (`Decimal(body.amount_recovered)`)
- **What breaks:** Money and rate fields on `CreateContractVersionIn` (fee schedule values, MPPR/bilateral/assistant percentages, percent-of-charge) and `RecordOutcomeIn.amount_recovered` are typed as plain `str` with no validator. A non-numeric value ("abc") passes Pydantic, then `Money()/Rate.percent()/Decimal()` raise `decimal.InvalidOperation`, which is uncaught → generic 500. Callers get an opaque server error instead of a 422, and a garbage contract-version request is indistinguishable from a real server fault. `amount_recovered` is also not constrained to be non-negative, so a negative "recovery" can be recorded (feeds confidence scoring / recovered-dollar metrics).
- **Reproduce:** `POST /contracts/{id}/versions` with `fee_schedule={"99213":"abc"}` (as admin) → 500. `POST /findings/{id}/outcome` with `amount_recovered="-5"` → accepted.
- **Fix:** Add Pydantic `field_validator`s that parse to `Decimal`/`Money` (rejecting non-numeric and negative amounts) so failures surface as 422 at the schema boundary before reaching the domain constructors.
- **Effort:** S

### [CLEAN] The rest of the input surface is well-validated
- **Evidence:** `tenant_id` is never accepted from the client on any path/query/body (api/auth.py:1-8) — resolved server-side from the token. Pagination is bounded (`ge=1, le=100`, `ge=0`). Resource ids are typed `UUID` (bad ids 422 automatically). Outcome is a `Literal` enum. `payer_id`/`name` have length `Field` constraints. Uploads go through a virus scan before processing (remittances route).

---

## Authorization / IDOR

### [LOW] Defense-in-depth inconsistency on finding-by-id lookups
- **File:** src/api/repository.py:744 (`list_packets` — bare `session.get(FindingModel, finding_id)`), :647 (`generate_packet`), vs. :783 (`record_finding_outcome`) and :801-802 (`decide_packet`) which add explicit `!= tenant_id` checks
- **What breaks:** `decide_packet` and `record_finding_outcome` defensively re-check `row.tenant_id == tenant_id` after fetching by primary key, but `list_packets` and `generate_packet` rely solely on RLS (they `session.get(...)` inside `tenant_session` with no app-level tenant check). If RLS is genuinely enforced in the live DB, all four are safe and cross-tenant access returns None; but the inconsistency means two paths have no application-layer backstop should RLS ever be misapplied (e.g. a future connection using an RLS-bypassing role). No exploitable IDOR was found *by construction* — see clean note — but the guard should be uniform.
- **Reproduce:** Compare the four methods; two check tenant, two do not.
- **Fix:** Add the same explicit `tenant_id` guard (or route all by-id reads through a tenant-scoped repository helper) so the app-layer check is uniform.
- **Effort:** S

### [CLEAN] RBAC matrix vs. routes, and classic IDOR
- **File:** src/security/rbac.py:44-80; all of src/api/routes/
- **Evidence:** Every route uses `require_permission(Action.X)` and each mapping is correct: reads → `READ_FINDING`/`READ_CONTRACT` (viewer+); CSV export → `EXPORT_WORKLIST` (biller/admin); outcome → `RECORD_FINDING_OUTCOME`; packet draft/approve → `DRAFT_/APPROVE_RECOVERY_PACKET` (biller/admin); contract writes → `MANAGE_CONTRACT` (admin only); audit-log/access-history → `READ_AUDIT_LOG`/`READ_PHI_ACCESS_LOG` (auditor/admin). `can()` is deny-by-default. Classic IDOR is structurally prevented: no endpoint accepts a `tenant_id` parameter, and every resource read is executed inside `tenant_session` (RLS) using the token-derived tenant. **Residual dependency:** this all rests on RLS actually being live — which this session cannot verify (no DB) and which hasn't been re-proven since Phase 12's schema changes. See Agent 6.

### [CLEAN] Audit / PHI-access logging is append-only at the grant level
- **File:** alembic/versions/0001_initial_schema.py:47-92
- **Evidence:** `audit_log` and `phi_access_log` are granted only `SELECT, INSERT` to `asc_app`, with `UPDATE, DELETE` explicitly `REVOKE`d; both carry RLS with `FORCE`. PHI reads (`get_finding_detail`, `generate_packet`, `list_packets`) write a `phi_access_log` row; state-changing operations write `audit_log`. Genuinely append-only for the application role.

---

## Dependencies

### [CLEAN — per baseline, with caveat]
- **Evidence:** `00-baseline.md` recorded `pip-audit` → "No known vulnerabilities found." Spot-checking `requirements.lock.txt` versions (cryptography 50.0.0, fastapi 0.141.1, pydantic 2.13.4, pyjwt 2.13.0, anthropic 0.120.2, opentelemetry 1.44.0) shows current, non-flagged releases. JWT decoding pins `algorithms=[HS256]` explicitly and checks a typed `type` claim (session.py:86-94,163-167), so there is no alg-confusion or "none"-alg exposure. **Caveat:** `pip-audit`/`gitleaks` were not re-run this session; the baseline result predates any lockfile change after Phase 10 CI. Re-run both in CI against the current commit before production.

---

## Summary of what is genuinely clean vs. gapped

- **Genuinely clean:** secrets in repo/history; synthetic test fixtures; exception-to-client PHI handling; tracing design; RBAC-to-route matrix and structural IDOR prevention; append-only audit logging; encryption-at-rest algorithm (AES-256-GCM envelope, per-value DEK); JWT alg pinning; dependency posture (per baseline).
- **Gapped:** no authentication/login path; MFA unenforceable and unstorable; rate-limit/lockout unwired; step-up re-auth unwired; client-edge TLS not codified; incomplete field-level encryption; KEK rotation impossible with the production adapter; LLM prompt discloses claim identifiers to a third party; redaction filter installed on a single logger; unvalidated money/rate inputs.
- **Cannot verify here (cross-ref Agent 6):** that RLS is actually enforced in the live database — the entire IDOR/tenant-isolation "clean" verdict is contingent on it.
